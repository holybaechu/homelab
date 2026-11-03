import assert from 'node:assert/strict';
import http2 from 'node:http2';

const origin = process.argv[2] ?? 'http://127.0.0.1:8080';
const cases = [
    { path: '/', accept: 'text/html', status: 200, html: true },
    { path: '/', accept: '*/*', status: 200 },
    { path: '/', accept: 'application/json', method: 'POST', status: 400 },
    { path: '/api/', accept: '*/*', status: 200 },
    { path: '/session', accept: 'application/json', method: 'POST', status: 400 },
];

function verify(test, status, contentType, body) {
    assert.equal(status, test.status, `${test.method ?? 'GET'} ${test.path}`);
    if (test.html) {
        assert.match(contentType, /text\/html/);
        assert.match(body, /<html/);
    } else {
        assert.match(contentType, /application\/json/);
        const data = JSON.parse(body);
        if (test.method === 'POST') {
            assert.equal(data.status, 'error');
        } else {
            assert.ok(data.cobalt, 'The web client must receive processing instance information');
        }
    }
}

for (const test of cases) {
    const response = await fetch(new URL(test.path, origin), {
        method: test.method,
        headers: { Accept: test.accept, 'Content-Type': 'application/json' },
        body: test.method === 'POST' ? JSON.stringify({ url: 'https://example.com/not-media' }) : undefined,
        signal: AbortSignal.timeout(10000),
    });
    verify(test, response.status, response.headers.get('content-type') ?? '', await response.text());
}
console.log('HTTP/1.1: browser navigation and processing requests passed');

const session = http2.connect(origin);
session.setTimeout(10000, () => session.destroy(new Error('HTTP/2 request timed out')));
try {
    for (const test of cases) {
        await new Promise((resolve, reject) => {
            const request = session.request({
                ':path': test.path,
                ':method': test.method ?? 'GET',
                accept: test.accept,
                'content-type': 'application/json',
            });
            let headers;
            let body = '';
            request.on('response', value => { headers = value; });
            request.setEncoding('utf8');
            request.on('data', value => { body += value; });
            request.on('error', reject);
            request.on('end', () => {
                try {
                    verify(test, headers[':status'], headers['content-type'] ?? '', body);
                    resolve();
                } catch (error) {
                    reject(error);
                }
            });
            request.end(test.method === 'POST' ? JSON.stringify({ url: 'https://example.com/not-media' }) : undefined);
        });
    }
    console.log('HTTP/2: browser navigation and processing requests passed');
} finally {
    session.close();
}
