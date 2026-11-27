"""Versioned, non-secret report-to-dashboard chart configuration."""


def percent(field):
    return 'CAST(`' + field + '`/100.0 AS DECIMAL(10,2))'


def specs(table):
    result = []
    def add(name, kind, sql, config, size='half', expected=None):
        result.append({'name': name, 'kind': kind, 'sql': sql, 'config': config,
                       'size': size, 'expected_rows': expected})
    for title, field in [('评测用例数', 'selected_cases'), ('已评分用例数', 'scored_cases'),
            ('同口径可比用例数', 'comparable_cases'), ('满分用例', 'pass_cases'),
            ('未满分已出分用例', 'partial_cases'), ('框架错误', 'framework_errors'),
            ('100秒内完成比例（%）', 'within_100_bps'), ('200秒内完成比例（%）', 'within_200_bps'),
            ('评分覆盖（%）', 'coverage_bps')]:
        add(title, 'metric', 'SELECT ' + (percent(field) if field.endswith('_bps') else field) +
            ' AS value FROM ' + table('overview'), {'value': 'value'}, 'third', 1)
    fields = ['模块', '用例数', '本次出分', '覆盖率', '历史模块分', '当前模块分', '可比数',
              '历史同口径分', '当前同口径分', '同口径变化']
    add('模块覆盖与历史同口径对照（模块内case等权）', 'table',
        'SELECT module_name AS `模块`,case_count AS `用例数`,current_scored AS `本次出分`,' +
        percent('coverage_bps') + ' AS `覆盖率`,' + percent('baseline_bps') + ' AS `历史模块分`,' +
        percent('current_bps') + ' AS `当前模块分`,paired_count AS `可比数`,' +
        percent('paired_baseline_bps') + ' AS `历史同口径分`,' + percent('paired_current_bps') +
        ' AS `当前同口径分`,' + percent('delta_bps') + ' AS `同口径变化` FROM ' + table('module_summary') +
        ' ORDER BY module_name', {'fields': fields}, 'full', 'module_summary')
    add('各模块当前得分（仅选中样本）', 'bar', 'SELECT module_name,' + percent('current_bps') +
        ' AS score FROM ' + table('module_summary') + ' WHERE current_bps IS NOT NULL ORDER BY module_name',
        {'x': 'module_name', 'y': 'score'})
    add('模块同口径分差（非自动回退结论）', 'bar', 'SELECT module_name,' + percent('delta_bps') +
        ' AS score_delta FROM ' + table('module_summary') + ' WHERE delta_bps IS NOT NULL ORDER BY delta_bps',
        {'x': 'module_name', 'y': 'score_delta'})
    add('逐用例同口径得分变化', 'bar', 'SELECT case_id,' + percent('delta_bps') +
        ' AS score_delta FROM ' + table('case_results') + ' WHERE delta_bps IS NOT NULL ORDER BY delta_bps,case_id',
        {'x': 'case_id', 'y': 'score_delta'}, 'full')
    add('历史与本次耗时对照（每点一条case，秒）', 'scatter',
        'SELECT case_id,baseline_ms/1000.0 AS baseline_seconds,current_ms/1000.0 AS current_seconds FROM ' +
        table('case_results') + ' WHERE duration_delta_ms IS NOT NULL AND baseline_ms IS NOT NULL AND current_ms IS NOT NULL',
        {'x': 'baseline_seconds', 'y': 'current_seconds', 'label': 'case_id'})
    add('当前逐用例任务耗时（秒）', 'bar', 'SELECT case_id,current_ms/1000.0 AS task_seconds FROM ' +
        table('case_results') + ' WHERE current_ms IS NOT NULL ORDER BY current_ms DESC', {'x': 'case_id', 'y': 'task_seconds'})
    for label, side in [('历史', 'baseline'), ('当前', 'current')]:
        add(label + '累计完成曲线（选中case为分母）', 'line',
            'SELECT threshold_seconds,' + percent(side + '_bps') + ' AS completion_percent FROM ' +
            table('duration_curve') + ' ORDER BY threshold_seconds',
            {'x': 'threshold_seconds', 'y': 'completion_percent'})
    add('100秒/200秒完成情况对照', 'table', 'SELECT threshold_seconds AS `阈值秒`,' + percent('baseline_bps') +
        ' AS `历史完成比例`,' + percent('current_bps') + ' AS `本次完成比例` FROM ' + table('duration_curve') +
        ' WHERE threshold_seconds IN (100,200) ORDER BY threshold_seconds',
        {'fields': ['阈值秒', '历史完成比例', '本次完成比例']}, 'full', 2)
    add('逐用例完整明细与比较资格', 'table', 'SELECT case_id AS `用例`,module_name AS `模块`,batch_label AS `来源批次`,title AS `任务`,verdict AS `结果`,' +
        percent('baseline_bps') + ' AS `历史得分`,' + percent('current_bps') + ' AS `本次得分`,' +
        percent('delta_bps') + ' AS `可比分差`,passed_checks AS `通过检查`,expected_checks AS `全部检查`,baseline_ms/1000.0 AS `历史秒`,current_ms/1000.0 AS `本次秒`,comparability AS `比较资格`,version AS `构建` FROM ' +
        table('case_results') + ' ORDER BY module_key,case_id',
        {'fields': ['用例', '模块', '来源批次', '任务', '结果', '历史得分', '本次得分', '可比分差', '通过检查', '全部检查', '历史秒', '本次秒', '比较资格', '构建']}, 'full', 'case_results')
    add('逐项失分（含未到达检查，非独立缺陷数）', 'table',
        'SELECT case_id AS `用例`,module_name AS `模块`,session_name AS `阶段`,rule_text AS `检查项`,detail_text AS `原因`,evidence_origin AS `证据来源`,unreached AS `未到达` FROM ' +
        table('failed_checks') + ' ORDER BY case_id,session_name',
        {'fields': ['用例', '模块', '阶段', '检查项', '原因', '证据来源', '未到达']}, 'full', 'failed_checks')
    add('v2检查点覆盖和通过率（各维度独立）', 'table',
        'SELECT checkpoint_id AS `检查点`,eligible AS `适用数`,scored AS `可判定`,passed AS `通过`,failed AS `未通过`,unscored AS `未评分`,not_applicable AS `不适用`,' +
        percent('coverage_bps') + ' AS `覆盖率`,' + percent('pass_rate_bps') + ' AS `通过率` FROM ' +
        table('v2_summary') + ' ORDER BY checkpoint_id',
        {'fields': ['检查点', '适用数', '可判定', '通过', '未通过', '未评分', '不适用', '覆盖率', '通过率']}, 'full', 'v2_summary')
    add('v2逐用例判定与证据引用', 'table', 'SELECT case_id AS `用例`,checkpoint_id AS `检查点`,status AS `判定`,reason_text AS `原因`,evidence_refs AS `证据引用` FROM ' +
        table('v2_details') + ' ORDER BY case_id,checkpoint_id',
        {'fields': ['用例', '检查点', '判定', '原因', '证据引用']}, 'full', 'v2_details')
    add('任务和Trace定位', 'table', 'SELECT case_id AS `用例`,task_id AS `任务ID`,trace_id AS `TraceID`,result_sha256 AS `证据SHA256` FROM ' +
        table('case_results') + ' ORDER BY case_id',
        {'fields': ['用例', '任务ID', 'TraceID', '证据SHA256']}, 'full', 'case_results')
    add('数据来源、模型、构建与口径', 'table', 'SELECT batch_label AS `来源`,case_count AS `用例数`,model_name AS `模型`,versions AS `构建`,source_run AS `原始运行`,scope_note AS `口径` FROM ' +
        table('provenance'), {'fields': ['来源', '用例数', '模型', '构建', '原始运行', '口径']}, 'full', 'provenance')
    add('SQL路径评分：功能原分、次优系数与最终分', 'table', 'SELECT case_id AS `用例`,' + percent('functional_bps') +
        ' AS `功能原分`,' + percent('path_factor_bps') + ' AS `路径系数`,' + percent('current_bps') +
        ' AS `最终分`,path_classification AS `路径`,scoring_policy AS `规则版本` FROM ' + table('path_scoring'),
        {'fields': ['用例', '功能原分', '路径系数', '最终分', '路径', '规则版本']}, 'full', 'path_scoring')
    add('低分及未评分归因分类（case数，非独立缺陷数）', 'bar', 'SELECT category,case_count FROM ' +
        table('cause_summary') + ' ORDER BY case_count DESC', {'x': 'category', 'y': 'case_count'}, 'full', 'cause_summary')
    add('低分及未评分case逐条归因与确认程度', 'table', 'SELECT case_id AS `用例`,' + percent('score_bps') +
        ' AS `得分`,grading_state AS `出分状态`,category AS `主因分类`,reason_text AS `原因`,confidence AS `确认程度`,secondary_text AS `补充说明`,failed_check_count AS `未通过检查数`,unreached_count AS `未到达数`,task_count AS `任务数` FROM ' +
        table('case_attribution') + ' ORDER BY category,case_id',
        {'fields': ['用例', '得分', '出分状态', '主因分类', '原因', '确认程度', '补充说明', '未通过检查数', '未到达数', '任务数']}, 'full', 'case_attribution')
    return result
