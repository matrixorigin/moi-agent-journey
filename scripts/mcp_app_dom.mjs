import fs from "node:fs";
import { JSDOM } from "jsdom";

const contract = JSON.parse(fs.readFileSync(0, "utf8"));
// jsdom does not execute module scripts. The resource remains byte-identical
// for hashing; only the in-memory DOM execution copy changes the tag type.
const html = fs.readFileSync(process.argv[2], "utf8")
  .replace(/<script([^>]*?)type="module"([^>]*)>/g, "<script$1$2>");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "https://localhost/" });
const { document } = dom.window;
await new Promise(resolve => setTimeout(resolve, 75));

const checks = [];
const add = (rule, passed, detail) => checks.push({ rule, passed: Boolean(passed), detail: passed ? "" : detail });
const text = () => {
  if (!document.body) return "";
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll("script,style").forEach(node => node.remove());
  return clone.textContent.replace(/\s+/g, " ").trim();
};
const expected = contract.expected || {};
const fields = expected.fields || {};
const submission = contract.submission || {};
const variables = submission.variables || {};
const substitute = value => typeof value === "string" ? value.replace(/\{(\w+)\}/g, (_, key) => String(variables[key] ?? `{${key}}`)) : value;
const containsAll = values => values.every(value => text().includes(String(value)));
const buttons = () => [...document.querySelectorAll("button")];
const visibleText = element => (element.textContent || "").replace(/\s+/g, " ").trim();

function checkComponent() {
  const component = expected.component;
  if (component === "Chart") {
    add("MCP App 包含真实柱状图", Boolean(document.querySelector("canvas,svg,.bar,[data-value],[data-chart],[role=img]")), "chart_missing");
    add("MCP App 图表类型和数据正确", fields.type === "bar" && containsAll(["North", "South", "Sales", "12", "7"]), "chart_data_mismatch");
  } else if (component === "Table") {
    add("MCP App 包含真实表格", Boolean(document.querySelector("table,[role=table]")), "table_missing");
    const rows = fields.rows || [];
    add("MCP App 表格记录和类型正确", rows.every(row => Object.entries(row).every(([key, value]) => text().includes(key) && text().includes(String(value)))), "table_data_mismatch");
  } else if (component === "Question") {
    add("MCP App 包含问题组件", Boolean(document.querySelector("form,[data-question],main,section")), "question_missing");
    const type = fields.type;
    if (type === "text") {
      add("MCP App 文本输入正确", Boolean(document.querySelector("input[type=text],input:not([type]),textarea")), "text_input_missing");
    } else {
      const config = submission.a2ui_submit || {};
      const labels = config.labels || [];
      const options = [...document.querySelectorAll("input[type=checkbox],input[type=radio]")];
      add("MCP App 选项标签和值完整", labels.every(label =>
        buttons().some(button => visibleText(button).includes(label)) ||
        options.some(option => option.value === label)), "question_options_mismatch");
    }
    add("MCP App 初始状态不泄露答案", !/答对了|回答正确|correct/i.test(text()), "answer_revealed_initially");
    const config = submission.a2ui_submit || {};
    const answer = config.answer;
    if (type === "text") {
      const input = document.querySelector("input[type=text],input:not([type]),textarea");
      if (input) {
        input.value = substitute(answer);
        input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
        input.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
      }
    } else {
      const answers = type === "multiple" ? answer : [answer];
      for (const value of answers) {
        const input = document.querySelector(`input[type=checkbox][value="${value}"],input[type=radio][value="${value}"]`);
        const button = buttons().find(item => visibleText(item) === String(value) || visibleText(item).includes(String(value)));
        if (input) input.click();
        else if (button) button.click();
      }
    }
    const form = document.querySelector("form");
    const submitButton = buttons().find(item => /提交|submit/i.test(visibleText(item)));
    if (submitButton) submitButton.click();
    if (form) form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    add("MCP App 真实提交后判定结果正确", /答对|回答正确|correct/i.test(text()), "interaction_result_incorrect");
    const candidates = buttons().filter(button => /4|5|2|3/.test(visibleText(button)));
    add("MCP App 提交后输入按预期锁定", candidates.every(button => button.disabled), "inputs_not_locked");
  } else if (component === "Form") {
    const config = submission.a2ui_form_submit || {};
    const expectedFields = config.fields || {};
    add("MCP App 包含项目和类型输入", Boolean(document.querySelector("input,textarea,select")), "form_fields_missing");
    const typeField = [...document.querySelectorAll("select")].find(select => [...select.options].some(option => option.value === "exact"));
    const exactButton = buttons().find(item => /精确|^exact$/i.test(visibleText(item)));
    const allButton = buttons().find(item => /全部|^all$/i.test(visibleText(item)));
    add("MCP App 类型选项精确包含 exact 和 all", Boolean((typeField && [...typeField.options].length === 2 && [...typeField.options].every(option => ["exact", "all"].includes(option.value))) || (exactButton && allButton)), "form_options_mismatch");
    const button = buttons().find(item => /提交|submit/i.test(visibleText(item)));
    add("MCP App 提交按钮绑定两个字段", Boolean(button && Object.keys(expectedFields).length === 2), "form_button_binding_missing");
    for (const [label, rawValue] of Object.entries(expectedFields)) {
      const value = Array.isArray(rawValue) ? rawValue[0] : rawValue;
      if (label === "项目") {
        const input = document.querySelector("input[type=text],input:not([type]),textarea");
        if (input) {
          input.value = substitute(value);
          input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
          input.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
        }
      } else if (label === "类型" && typeField) {
        typeField.value = value;
        typeField.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
      }
    }
    if (button) button.click();
    const form = document.querySelector("form");
    if (form) form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    const projectInput = document.querySelector("input[type=text],input:not([type]),textarea");
    add("MCP App 表单提交值正确", Boolean(projectInput && projectInput.value === variables.code && ((typeField && typeField.value === "exact") || exactButton)), "form_submission_values_mismatch");
  } else {
    add("MCP App 组件类型受支持", false, "unsupported_component:" + component);
  }
}

checkComponent();
dom.window.close();
console.log("__MCP_APP_CHECKS__" + JSON.stringify(checks));
