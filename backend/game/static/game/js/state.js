/**
 * State management — Game.state namespace
 */
(function () {
  "use strict";

  window.Game = window.Game || {};

  var MONTH_NAMES = [
    "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月"
  ];
  var MAX_MONTH = 36;

  window.Game.state = {
    user: null,
    games: [],
    currentGame: null,
    lastReport: null,
    activeNegotiation: null,
    agents: [],
  };

  window.Game.MAX_MONTH = MAX_MONTH;

  window.Game.seasonName = function (n) {
    if (n > MAX_MONTH) return "任期结束";
    var year = Math.ceil(n / 12);
    var idx = (n - 1) % 12;
    return "第" + year + "年·" + MONTH_NAMES[idx] + "（第" + n + "月）";
  };

  window.Game.setGame = function (data) {
    Game.state.currentGame = data;
    Game.components.renderHeader();
    Game.components.renderDashboard();
    Game.components.renderVillages();
    Game.components.renderInvestTab();
    // Check for active negotiation
    if (Game.components.checkActiveNegotiation) {
      Game.components.checkActiveNegotiation();
    }
    // Load agents for village table and profile modal
    if (Game.api && data && data.id) {
      Game.api.getAgents(data.id).then(function (agents) {
        Game.state.agents = agents;
        Game.components.renderVillages();
      }).catch(function () {
        Game.state.agents = [];
      });
    }
  };
})();
