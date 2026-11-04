import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const loader = process.env.COBALT_YOUTUBE_LOADER ?? '/compat/youtube_client.mjs';
const origin = 'http://127.0.0.1:9191';
const child = spawn(process.execPath, ['--import', loader, 'src/cobalt.js'], {
    cwd: '/app',
    env: {
        ...process.env,
        API_URL: origin + '/',
        API_PORT: '9191',
        API_LISTEN_ADDRESS: '127.0.0.1',
        CUSTOM_INNERTUBE_CLIENT: 'VISIONOS',
    },
    stdio: 'ignore',
});

try {
    let info;
    for (let attempt = 0; attempt < 50; attempt++) {
        if (child.exitCode !== null || child.signalCode !== null) break;
        try {
            const response = await fetch(origin, { signal: AbortSignal.timeout(1000) });
            if (response.ok) {
                info = await response.json();
                break;
            }
        } catch {}
        await delay(100);
    }
    assert.ok(info?.cobalt, 'Cobalt must start successfully with the VISIONOS client');
    const response = await fetch(origin, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: 'https://example.com/not-media' }),
        signal: AbortSignal.timeout(5000),
    });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, 'error.api.link.invalid');
    console.log('Cobalt starts with VISIONOS and serves processing requests');
} finally {
    child.kill('SIGTERM');
    await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(3000)]);
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
}
