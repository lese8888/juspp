import fs from 'node:fs/promises';

import { renderCollectSummary } from '../lib/collect-summary.mjs';

const summaryPath = process.env.COLLECT_SUMMARY_FILE;
const outputPath = process.env.GITHUB_STEP_SUMMARY;
let summary = null;

if (summaryPath) {
  try {
    summary = JSON.parse(await fs.readFile(summaryPath, 'utf8'));
  } catch {
    summary = null;
  }
}

const markdown = `${renderCollectSummary(summary)}\n`;
if (outputPath) {
  await fs.appendFile(outputPath, markdown, 'utf8');
} else {
  process.stdout.write(markdown);
}
