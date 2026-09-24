const {test} = require('node:test');
const assert = require('node:assert/strict');
const m = require(process.argv[2]);
const from = '2026-09-23T08:00', to = '2026-09-23T09:00';
test('multiband retains UTC conversion and explicit product',()=>{
 for(const preset of ['x_qc','sx_composite']){
  const s=m.parseSelection(preset,from,to,'S1,X1','','test','local');
  assert.equal(s.product_id,'local');assert.equal(s.start,'2026-09-23T00:00:00.000Z');
  assert.deepEqual(s.radar_ids,['s1','x1']);
 }
});
test('multiband bounded range does not shrink legacy range',()=>{
 assert.throws(()=>m.parseSelection('sx_composite',from,'2026-09-23T10:00','x1','','test'));
 assert.equal(m.parseSelection('qc_preview',from,'2026-09-23T10:00','s1','','test').preset,'qc_preview');
});
test('unknown presets duplicate radars and invalid products rejected',()=>{
 assert.throws(()=>m.parseSelection('typo',from,to,'x1','','test'));
 assert.throws(()=>m.parseSelection('sx_composite',from,to,'x1,x1','','test'));
 assert.throws(()=>m.parseSelection('sx_composite',from,to,'x1','','test','../unsafe'));
});
test('deep link supports new presets without action',()=>{
 assert.equal(m.viewFromSearch('?view=new&preset=x_qc').preset,'x_qc');
 assert.equal(m.viewFromSearch('?view=new&preset=sx_composite').preset,'sx_composite');
 assert.equal(m.viewFromSearch('?view=new&preset=delete-all').preset,undefined);
});
test('new metrics are labelled without claiming peak RSS',()=>{
 assert.match(m.metricLabel('resident_input_bytes'),/非进程峰值/);
 assert.match(m.metricLabel('fusion_ms'),/组合/);
});
