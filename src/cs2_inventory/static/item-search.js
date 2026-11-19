/* 同一件饰品的显示名和市场名共享关键词；搜索不参与物品归类。 */
(() => {
  'use strict';
  const normalize = text => text.normalize('NFKC').toLowerCase().replace(/[\p{Pd}\u2212]/gu, '');
  const separators = /[\s\p{P}|]+/gu;
  function compile(query) {
    const terms = normalize(query).split(separators).filter(Boolean);
    if (!terms.length) return () => true;
    return (...names) => {
      const forms = names.filter(Boolean).map(name => normalize(name).replace(separators, ''));
      return terms.every(term => forms.some(name => name.includes(term)));
    };
  }
  window.InventoryItemSearch = {compile};
})();
