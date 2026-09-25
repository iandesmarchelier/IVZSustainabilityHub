const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync('index.html', 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(x=>x[1]).filter(x=>x.includes('buildInitialState'));
// The page touches the DOM while it loads (e.g. the boot logo): any element, property or call is a harmless stand-in.
const element = new Proxy(function () {}, {
  get: (target, key) => key === Symbol.toPrimitive ? () => '' : element,
  set: () => true,
  apply: () => element,
});
const context = vm.createContext({console, window: {}, document: element, setTimeout() {}});
vm.runInContext(scripts[0], context);
fs.mkdirSync('data', {recursive:true});
vm.runInContext('const testSeed = buildInitialState()', context);
fs.writeFileSync('data/seed.json', vm.runInContext('JSON.stringify(testSeed)', context));
fs.writeFileSync('data/expected.json', vm.runInContext('JSON.stringify(METRICS.map(m => ({id:m.id,value:computeMetric(testSeed,m.id,{year:2026,loc:"GRP",s2:"Market-based"})})))', context));
console.log('Extracted frontend seed.');
