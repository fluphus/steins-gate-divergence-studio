# Bundled dependency

`mediabunny-1.62.1.js` is Mediabunny 1.62.1 bundled as a browser IIFE with esbuild 0.25.12. It is loaded locally so MP4 export also works when opening `index.html` from disk. No remote scripts are loaded.

- Project and source: https://github.com/Vanilagy/mediabunny
- Exact source distribution (includes `src/`): https://registry.npmjs.org/mediabunny/-/mediabunny-1.62.1.tgz
- License: Mozilla Public License 2.0; see `mediabunny-LICENSE.txt`.
- Upstream library source is unmodified. Browser bundling command:

```sh
npx --yes --package=esbuild@0.25.12 esbuild package/dist/bundles/mediabunny.mjs --bundle --format=iife --global-name=Mediabunny --minify --outfile=mediabunny-1.62.1.js
```
