/**
 * Core constants and helpers — Game.components namespace (partial)
 */
(function () {
  "use strict";

  window.Game = window.Game || {};
  window.Game.components = window.Game.components || {};

  // 每千人基础医疗费用（非线性递增）
  var MEDICAL_COST_PER_THOUSAND = [0, 5, 12, 22];
  var MEDICAL_NAMES = ["无", "简易医馆", "县医署", "完善医疗"];

  function calcMedicalCost(level, county) {
    var pop = 0;
    (county.villages || []).forEach(function (v) { pop += v.population; });
    var pi = county.price_index || 1.0;
    return Math.round(MEDICAL_COST_PER_THOUSAND[level] * (pop / 1000) * pi);
  }

  var DISASTER_NAMES = {
    flood: "洪灾",
    drought: "旱灾",
    locust: "蝗灾",
    plague: "疫病",
  };

  var INVEST_DEFS = [
    { action: "reclaim_land", name: "开垦荒地", cost: 50, desc: "为目标村庄增加800亩耕地，下个秋季完成", needsVillage: true },
    { action: "build_irrigation", name: "修建水利", cost: 100, desc: "提升水利等级，减少洪灾风险，提高产量（8季完成）", needsVillage: false },
    { action: "expand_school", name: "扩建县学", cost: 80, desc: "文教+10（8季完成）", needsVillage: false },
    { action: "fund_village_school", name: "资助村塾", cost: 30, desc: "为目标村庄建立村塾，民心+5（4季完成）", needsVillage: true },
    { action: "hire_bailiffs", name: "增设衙役", cost: 40, desc: "治安+8，年行政开支+40两（立即生效）", needsVillage: false },
    { action: "repair_roads", name: "修缮道路", cost: 60, desc: "商业+8（1季完成）", needsVillage: false },
    { action: "build_granary", name: "开设义仓", cost: 70, desc: "灾害损失减半（立即生效）", needsVillage: false },
    { action: "relief", name: "赈灾救济", cost: 80, desc: "民心+8，秋季灾害损失减半（立即生效）", needsVillage: false },
  ];

  function el(id) { return document.getElementById(id); }
  function h(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function showToast(msg, type) {
    type = type || "info";
    var container = el("toast-container");
    var toast = h("div", "toast toast-" + type, msg);
    container.appendChild(toast);
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 3000);
  }

  // Export core utilities
  var C = Game.components;
  C.MEDICAL_COST_PER_THOUSAND = MEDICAL_COST_PER_THOUSAND;
  C.MEDICAL_NAMES = MEDICAL_NAMES;
  C.calcMedicalCost = calcMedicalCost;
  C.DISASTER_NAMES = DISASTER_NAMES;
  C.INVEST_DEFS = INVEST_DEFS;
  C.el = el;
  C.h = h;
  C.escapeHtml = escapeHtml;
  C.showToast = showToast;
})();
