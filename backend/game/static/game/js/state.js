/**
 * State management — Game.state namespace
 */
(function () {
  "use strict";

  window.Game = window.Game || {};

  var SEASON_NAMES = ["春", "夏", "秋", "冬"];

  window.Game.state = {
    user: null,
    games: [],
    currentGame: null,
    lastReport: null,
    activeNegotiation: null,
    agents: [],
  };

  window.Game.seasonName = function (n) {
    if (n > 12) return "任期结束";
    var year = Math.ceil(n / 4);
    var idx = (n - 1) % 4;
    return "第" + year + "年 · " + SEASON_NAMES[idx] + "（第" + n + "季度）";
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
