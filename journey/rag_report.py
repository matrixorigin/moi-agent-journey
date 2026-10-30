"""Present answer quality separately from execution success and observability."""
from collections import Counter, defaultdict


METRICS = {'correctness': '参考要点满足率', 'faithfulness': '已审查主张的检索依据支持率',
           'citation_support': '引用支持结论的比例', 'citation_integrity': '引用身份及来源有效率'}


def _ratio(passed, total):
    return {'passed': passed, 'total': total, 'rate': passed / total if total else None}


def _metrics(answers):
    result = {}
    for name in METRICS:
        values = [answer.get('metrics', {}).get(name, {}) for answer in answers]
        result[name] = _ratio(sum(value.get('passed', value.get('valid', 0)) for value in values),
                              sum(value.get('total', 0) for value in values))
    return result


def valid_rag_result(rag):
    if (not isinstance(rag, dict) or not isinstance(rag.get('corpus'), str) or
            type(rag.get('expected_answers')) is not int or rag['expected_answers'] < 0 or
            not isinstance(rag.get('answers'), list)):
        return False
    evaluated = 0
    for answer in rag['answers']:
        if (not isinstance(answer, dict) or answer.get('evaluation_state') not in {'scored', 'unavailable', 'context_only'} or
                any(not isinstance(answer.get(key), str) for key in ('session', 'phase', 'question', 'text', 'task_id'))):
            return False
        evaluated += answer['evaluation_state'] != 'context_only'
        metrics = answer.get('metrics', {})
        if not isinstance(metrics, dict):
            return False
        for name, value in metrics.items():
            if name not in METRICS or not isinstance(value, dict):
                return False
            passed, total = value.get('passed', value.get('valid')), value.get('total')
            if (type(passed) is not int or type(total) is not int or not 0 <= passed <= total or
                    value.get('rate') != (passed / total if total else None)):
                return False
        if answer['evaluation_state'] == 'scored' and (set(metrics) != set(METRICS) or
                not isinstance(answer.get('verdict'), dict) or not metrics['correctness']['total']):
            return False
    return evaluated <= rag['expected_answers']


def summarize_rag(rows):
    expected = sum(row.get('rag', {}).get('expected_answers', 0) for row in rows)
    answers = [(row['case_id'], row['rag'], answer) for row in rows if isinstance(row.get('rag'), dict)
               for answer in row['rag'].get('answers', []) if answer.get('evaluation_state') != 'context_only']
    states = Counter(answer.get('evaluation_state', 'unavailable') for _, _, answer in answers)
    groups, corpora = defaultdict(list), defaultdict(list)
    for case_id, rag, answer in answers:
        corpora[rag['corpus']].append(answer)
        if rag.get('comparison_group') and answer.get('evaluation_state') == 'scored':
            groups[rag['comparison_group']].append({'case_id': case_id,
                'phase': answer['phase'], 'seconds': answer.get('seconds'),
                'criteria': {item['id']: item['passed'] for item in answer['verdict']['criteria']}})
    return {'answers_expected': expected, 'answers_observed': len(answers), 'answers_missing': expected - len(answers),
            'evaluation_states': dict(states), 'metrics': _metrics([answer for _, _, answer in answers]),
            'by_corpus': {name: {'answers': len(values), 'metrics': _metrics(values),
                'evaluation_states': dict(Counter(value.get('evaluation_state', 'unavailable') for value in values))}
                for name, values in sorted(corpora.items())},
            'efficiency': dict(Counter(answer.get('efficiency', {}).get('review', {}).get('assessment', 'unavailable')
                                       for _, _, answer in answers)),
            'out_of_scope_chunks': sum(answer.get('observations', {}).get('out_of_scope_chunks', 0)
                                       for _, _, answer in answers),
            'comparisons': [{'group': key, 'answers': items,
                             'criteria_consistent': all(
                                 len({item['criteria'][criterion] for item in items}) == 1
                                 for criterion in set.intersection(*(set(item['criteria']) for item in items))),
                             'shared_criteria': sorted(set.intersection(*(set(item['criteria']) for item in items)))}
                            for key, items in sorted(groups.items()) if len(items) > 1]}


def case_lines(result, cell):
    rag = result.get('rag')
    if not isinstance(rag, dict):
        return []
    lines = ['', '## 知识库问答', '', f"知识库组：{cell(rag['corpus'])}。",
             '参考要点按语义判定，允许同义表达；标识符、数值、单位和适用范围须准确。',
             '主张支持率仅检查已观测检索片段；未观测到检索上下文时显示无法评价。']
    if rag.get('target'):
        lines.append(f"被测智能体：{cell(rag['target']['agent_id'])}；绑定知识库组：{cell(rag['target']['corpora'])}。")
    for key in ('qa_version_start', 'qa_version_end'):
        version = result.get(key, {})
        lines.append(f"- {'开始' if key.endswith('start') else '结束'}版本：{cell(version.get('version'))} / {cell(version.get('git_commit'))}。")
    for answer in rag.get('answers', []):
        lines += ['', f"### {cell(answer['session'])} / {cell(answer['phase'])}", '',
                  f"问题：{cell(answer['question'], 10000)}", '',
                  f"耗时：{cell(answer.get('seconds'))} 秒；评估：{cell(answer.get('evaluation_state'))}。",
                  f"会话：{cell(answer.get('conversation_id'))}；Trace：{cell(answer.get('trace_id'))}。", '',
                  '**效率**', '']
        efficiency = answer.get('efficiency', {})
        review = efficiency.get('review', {})
        lines += [f"工具调用 {cell(efficiency.get('tool_calls'))} 次，轮次 {cell(efficiency.get('tool_rounds'))}；"
                  f"完全相同参数的重复调用组 {len(efficiency.get('exact_repetitions', []))}；"
                  f"连续检索未获得新片段的最长次数 {cell(efficiency.get('max_consecutive_retrievals_without_new_chunks'))}。",
                  f"轨迹评估：{cell(review.get('assessment', 'unavailable'))}；{cell(review.get('summary'))}。"]
        for finding in review.get('findings', []):
            lines.append(f"- {cell(finding['kind'])}：{cell(finding['reason'], 4000)}；调用 {cell(finding['call_ids'])}。")
        lines += ['', '**正确性**', '', '**实际最终回答**', '', answer.get('text', '')]
        if answer.get('evaluation_reason'):
            lines += ['', '无法完整评价的原因：' + cell(answer['evaluation_reason'])]
        if answer.get('metrics'):
            lines += ['', '| 质量维度 | 满足／已检查 | 比例 |', '|---|---:|---:|']
            for name, label in METRICS.items():
                value = answer['metrics'].get(name, {'passed': 0, 'total': 0, 'rate': None})
                rate = f"{value['rate']:.1%}" if value.get('rate') is not None else '无法评价'
                lines.append(f"| {label} | {value.get('passed', value.get('valid', 0))}/{value['total']} | {rate} |")
        observed = answer.get('observations', {})
        citations = observed.get('citations', [])
        lines += ['', f"检索：{observed.get('retrieval_calls', 0)} 次可识别调用；"
                  f"{len(observed.get('retrieved_chunks', []))} 个返回片段；结构化引用 {len(citations)} 个。"]
        for citation in citations:
            ref = citation['reference']
            lines.append(f"- 引用 {citation['index']}：{cell(ref.get('file_name'))}；"
                         f"知识库 {cell(ref.get('semantic_model_id'))}；来源 {cell(ref.get('source_row_id'))}；"
                         f"分段 {cell(ref.get('chunk_id', ref.get('chunk_ids')))}；"
                         f"来源有效 {citation['integrity']}；检索关联 {cell(citation.get('retrieval_link'))}。")
            if citation.get('source_error'):
                lines.append('  来源读取错误：' + cell(citation['source_error']))
        verdict = answer.get('verdict', {})
        for item in verdict.get('criteria', []):
            lines.append(f"- {item['id']} {'满足' if item['passed'] else '未满足'}：{cell(item['reason'], 4000)}")
        for item in verdict.get('claims', []) + verdict.get('citations', []):
            if item.get('supported') is False:
                lines.append(f"- 依据不足：{cell(item.get('answer_quote'))} — {cell(item['reason'], 4000)}")
    lines += ['', '### 完整工具轨迹', '',
              '以下包含未正常结束任务的已采集轨迹；重复调用只标记为信号，不能单独证明绕路。', '',
              '| 任务／阶段 | 调用 ID | 工具 | 参数 | 结果 | 秒 | 新检索片段 | 事件序号 |',
              '|---|---|---|---|---|---:|---:|---|']
    for trajectory in rag.get('trajectories', []):
        for call in trajectory['timeline']:
            lines.append(f"| {cell(trajectory['phase'])} | {cell(call['call_id'])} | {cell(call['tool'])} | "
                         f"{cell(call['arguments'])} | {cell(call['outcome'])} | {cell(call['seconds'])} | "
                         f"{call['new_retrieved_chunks']} | {cell(call['seqs'])} |")
    return lines


def aggregate_lines(report, cell):
    rag = report.get('rag')
    if not rag:
        return []
    lines = ['', '## 知识库问答质量', '',
             f"预期 {rag['answers_expected']} 个待评估回答，已记录 {rag['answers_observed']} 个，缺失 {rag['answers_missing']} 个；"
             f"评估状态：{cell(rag['evaluation_states'])}。",
             '', '### 效率', '', f"轨迹评估：{cell(rag['efficiency'])}。",
             '耗时、工具轮次和错误统计见上方；重复、发散及不收敛的具体证据见各 case 调用轨迹。',
             '', '### 正确性', '',
             '各比例仅统计有有效评估的对象，未评估项不按通过处理；完整状态与缺失原因见单 case 报告。', '',
             '| 质量维度 | 满足／已检查 | 比例 |', '|---|---:|---:|']
    for name, label in METRICS.items():
        value = rag['metrics'][name]
        rate = f"{value['rate']:.1%}" if value['rate'] is not None else '无法评价'
        lines.append(f"| {label} | {value['passed']}/{value['total']} | {rate} |")
    for suite in report.get('journeys', []):
        if suite.get('rag'):
            lines.append(f"- {cell(suite['display_name'])}：{cell(suite['rag']['evaluation_states'])}。")
    for name, group in rag['by_corpus'].items():
        lines.append(f"- {cell(name)}：{group['answers']} 个回答；评估状态 {cell(group['evaluation_states'])}；"
                     f"参考要点 {group['metrics']['correctness']['passed']}/{group['metrics']['correctness']['total']}。")
    for comparison in rag['comparisons']:
        lines.append(f"- 对照 {cell(comparison['group'])}：参考要点满足情况"
                     f"{'一致' if comparison['criteria_consistent'] else '有变化'}；{cell(comparison['answers'], 5000)}。")
    return lines
