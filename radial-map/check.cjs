const html = require('fs').readFileSync(process.argv[2], 'utf8');
const js = html.split('<script>')[1].split('</script>')[0];
const data = JSON.parse(html.split('id="data">')[1].split('</script>')[0]);
const layout = new Function(js + '; return layout')();
layout(data);
const pts = data.flatMap(c => c.pts.map(p => [p, c.id]));
let min = 1e9;
for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++)
  min = Math.min(min, Math.hypot(pts[i][0][0] - pts[j][0][0], pts[i][0][1] - pts[j][0][1]));
console.assert(min >= 14.9, 'dots overlap', min);
console.assert(data.every(c => !c.parent || (c.forkAt >= 0 && c.forkAt < data.find(p => p.id === c.parent).msgs.length)), 'bad fork');
console.log('layout ok: dots', pts.length, 'closest', min.toFixed(1), 'longest radius', Math.max(...data.map(c => c.end)));
