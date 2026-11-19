/* node tests/test_item_search_js.js */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = {window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/cs2_inventory/static/item-search.js'), 'utf8'), context);
const {compile} = context.window.InventoryItemSearch;

const items = [
  ['AK-47 | 红线（久经沙场）', 'AK-47 | Redline (Field-Tested)'],
  ['AK-47 | 红线（略有磨损）', 'AK-47 | Redline (Minimal Wear)'],
  ['AWP | 二西莫夫（久经沙场）', 'AWP | Asiimov (Field-Tested)'],
];
const selected = q => {
  const matches = compile(q);
  return items.flatMap((names, i) => matches(...names) ? [i] : []);
};
assert.deepEqual(selected(''), [0, 1, 2]);
assert.deepEqual(selected('ＡＫ４７　红线'), [0, 1]);
assert.deepEqual(selected('  红线  久经 '), [0]);
assert.deepEqual(selected('红线 AＫ‑４７'), [0, 1]);
assert.deepEqual(selected('REDLINE fieldtested'), [0]);
assert.deepEqual(selected('略有 REDLINE'), [1]);
assert.deepEqual(selected('久经 minimal'), []); // 不跨磨损、不跨物品凑关键词。
assert.deepEqual(selected('AWP 红线'), []);
assert.equal(compile('久经 ak47')(items[0][0]), true);
assert.equal(compile('redline')(items[0][0], null), false); // 无市场名时使用已有显示名。
assert.equal(compile('★')('★ 蝴蝶刀'), true);
assert.equal(compile('★')('AK-47'), false);
console.log('ITEM_SEARCH width/dash/case/multi/order/aliases/wear/row-scope=PASS');
