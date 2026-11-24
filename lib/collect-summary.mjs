export function renderCollectSummary(summary) {
  if (!summary) {
    return [
      '## 游闻采集摘要',
      '',
      '- 结果：未生成摘要，请查看采集步骤日志。'
    ].join('\n');
  }

  const statusLabels = {
    ok: '成功',
    unchanged: '无变化',
    failed: '失败'
  };
  const lines = [
    '## 游闻采集摘要',
    '',
    `- 结果：${statusLabels[summary.status] || summary.status || '未知'}`,
    `- 检查时间：${summary.checkedAt || '—'}`,
    `- 新闻总数：${summary.articleCount ?? '—'}`,
    `- 过去 24 小时：${summary.last24hCount ?? '—'}`,
    `- 香港当天发布：${summary.todayHktCount ?? '—'}（${summary.todayHktDate || '—'}）`,
    `- 最新原文发布：${summary.latestPublishedAt || '—'}`,
    `- 来源成功：${summary.successfulSourceCount ?? '—'}/${summary.sourceCount ?? '—'}`
  ];

  if (summary.error) {
    lines.push(`- 错误：${summary.error}`);
  }
  if (summary.failedSources?.length) {
    lines.push('', '### 失败来源', '');
    for (const source of summary.failedSources) {
      lines.push(`- ${source.name}：${source.error}`);
    }
  }
  return lines.join('\n');
}
