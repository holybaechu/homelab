'use strict';
const fs = require('node:fs');

function render(webhook, perplexity) {
  if (typeof webhook !== 'string' || !webhook || typeof perplexity !== 'string' || !perplexity) {
    throw new Error('Invalid credential input');
  }
  return JSON.stringify([
    {id: 'hermesWebhookTok', name: 'Hermes webhook', type: 'httpHeaderAuth',
      data: {name: 'X-Gitlab-Token', value: webhook}},
    {id: 'perplexityDecider', name: 'Perplexity Decider', type: 'perplexityApi',
      data: {apiKey: perplexity}},
  ]) + '\n';
}

module.exports = {render};
if (require.main === module) {
  process.umask(0o077);
  try {
    const decode = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true});
    const output = render(decode.decode(fs.readFileSync('/credentials/webhook-secret')),
      decode.decode(fs.readFileSync('/credentials/perplexity-api-key')));
    fs.writeFileSync('/config/credentials.json', output, {flag: 'wx', mode: 0o600});
  } catch {
    console.error('n8n credential rendering failed');
    process.exitCode = 1;
  }
}
