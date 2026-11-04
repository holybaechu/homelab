import { createRequire, registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// Compatibility with upstream PR #1581 until the official image includes
// youtubei.js 18 and VISIONOS: https://github.com/imputnet/cobalt/pull/1581
const dependencies = process.env.COBALT_YOUTUBE_DEPENDENCIES ?? '/youtube/package.json';
const youtubeURL = pathToFileURL(createRequire(dependencies).resolve('youtubei.js')).href;
const originalClients = "['IOS', 'ANDROID', 'YTSTUDIO_ANDROID', 'YTMUSIC_ANDROID']";
const updatedClients = "['IOS', 'ANDROID', 'ANDROID_VR', 'VISIONOS', 'YTSTUDIO_ANDROID', 'YTMUSIC_ANDROID']";

registerHooks({
    resolve(specifier, context, nextResolve) {
        return nextResolve(specifier === 'youtubei.js' ? youtubeURL : specifier, context);
    },
    load(url, context, nextLoad) {
        if (url !== 'file:///app/src/processing/services/youtube.js') {
            return nextLoad(url, context);
        }
        const source = readFileSync(new URL(url), 'utf8');
        if (source.split(originalClients).length !== 2) {
            throw new Error('Cobalt YouTube code changed; review whether the compatibility layer is still needed');
        }
        return { format: 'module', source: source.replace(originalClients, updatedClients), shortCircuit: true };
    },
});
