/**
 * Core constants and helpers — Game.components namespace (partial)
 */
(function () {
  "use strict";

  window.Game = window.Game || {};
  window.Game.components = window.Game.components || {};

  var INFRA_MAX_LEVEL = 3;

  var DISASTER_NAMES = {
    flood: "洪灾",
    drought: "旱灾",
    locust: "蝗灾",
    plague: "疫病",
  };

  var INVEST_DEFS = [
    { action: "reclaim_land", name: "开垦荒地", cost: 50, desc: "为目标村庄增加800亩耕地，下个九月（秋收）完成", needsVillage: true },
    { action: "build_irrigation", name: "修建水利", cost: null, desc: "提升水利等级(最高3级)，减少洪灾风险，提高产量，费用按耕地规模计算", needsVillage: false, infra: "irrigation" },
    { action: "expand_school", name: "扩建县学", cost: null, desc: "提升县学等级(最高3级)，文教+10", needsVillage: false, infra: "school" },
    { action: "build_medical", name: "建设医疗", cost: null, desc: "提升医疗等级(最高3级)，降低疫病风险和人口损失", needsVillage: false, infra: "medical" },
    { action: "fund_village_school", name: "资助村塾", cost: 30, desc: "为目标村庄建立村塾，民心+5（4个月完成）", needsVillage: true },
    { action: "hire_bailiffs", name: "增设衙役", cost: 40, desc: "治安+8，年行政开支+40两（立即生效）", needsVillage: false },
    { action: "repair_roads", name: "修缮道路", cost: 60, desc: "商业提升（首次+8，逐次递减，2个月完成）", needsVillage: false },
    { action: "build_granary", name: "开设义仓", cost: 70, desc: "秋季灾害人口损失×0.65（立即生效）", needsVillage: false },
    { action: "relief", name: "赈灾救济", cost: 80, desc: "民心+8，秋季灾害人口损失×0.65（立即生效）", needsVillage: false },
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

  var ADMIN_COST_LABELS = {
    official_salary: "官员俸禄",
    deputy_salary: "县丞俸禄",
    advisor_fee: "师爷束脩",
    clerks_cost: "六房书办",
    bailiff_cost: "衙役饷银",
    school_cost: "县学经费",
    office_cost: "衙署杂费",
    irrigation_maint: "水利维护",
    medical_maint: "医疗维护",
  };

  // Export core utilities
  var C = Game.components;
  C.INFRA_MAX_LEVEL = INFRA_MAX_LEVEL;
  C.DISASTER_NAMES = DISASTER_NAMES;
  C.INVEST_DEFS = INVEST_DEFS;
  C.el = el;
  C.h = h;
  C.escapeHtml = escapeHtml;
  C.showToast = showToast;
  C.ADMIN_COST_LABELS = ADMIN_COST_LABELS;
})();
