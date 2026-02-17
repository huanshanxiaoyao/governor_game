/**
 * DOM rendering — Game.components namespace
 */
(function () {
  "use strict";

  window.Game = window.Game || {};

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

  function renderHeader() {
    var g = Game.state.currentGame;
    if (!g) return;
    el("season-display").textContent = Game.seasonName(g.current_season);
    el("treasury-display").textContent = "县库: " + Math.round(g.county_data.treasury) + " 两";

    // Update tax slider to match current rate
    var slider = el("tax-slider");
    var display = el("tax-display");
    if (slider && g.county_data.tax_rate) {
      slider.value = Math.round(g.county_data.tax_rate * 100);
      display.textContent = Math.round(g.county_data.tax_rate * 100) + "%";
    }
  }

  function renderDashboard() {
    var g = Game.state.currentGame;
    if (!g) return;
    var c = g.county_data;

    // Stats grid
    var grid = el("stats-grid");
    grid.innerHTML = "";
    var stats = [
      { label: "民心", value: Math.round(c.morale) },
      { label: "治安", value: Math.round(c.security) },
      { label: "商业", value: Math.round(c.commercial) },
      { label: "文教", value: Math.round(c.education) },
    ];
    stats.forEach(function (s) {
      var card = h("div", "stat-card",
        '<div class="stat-label">' + s.label + '</div>' +
        '<div class="stat-value">' + s.value + '</div>');
      grid.appendChild(card);
    });

    // Info row
    var info = el("info-row");
    info.innerHTML = "";
    var medicalNames = ["无", "简易医馆", "县医署", "完善医疗"];
    var medicalCosts = [0, 25, 50, 100];
    var ml = c.medical_level || 0;
    var items = [
      { label: "税率", value: Math.round(c.tax_rate * 100) + "%" },
      { label: "水利等级", value: c.irrigation_level + "/2" },
      { label: "衙役等级", value: c.bailiff_level + "/3" },
      { label: "义仓", value: c.has_granary ? "已建" : "未建" },
      { label: "医疗", value: medicalNames[ml] + "(" + medicalCosts[ml] + "两/年)" },
      { label: "行政开支", value: c.admin_cost + "两/年" },
    ];
    items.forEach(function (it) {
      var span = h("span", "info-item", "<strong>" + it.label + ":</strong> " + it.value);
      info.appendChild(span);
    });

    // Environment
    var envDiv = el("environment-info");
    envDiv.innerHTML = "";
    if (c.environment) {
      var title = h("h4", "section-title", "环境");
      envDiv.appendChild(title);
      var row = h("div", "env-row");
      var envItems = [
        { label: "农业适宜度", value: (c.environment.agriculture_suitability * 100).toFixed(0) + "%" },
        { label: "水患风险", value: (c.environment.flood_risk * 100).toFixed(0) + "%" },
        { label: "边患风险", value: (c.environment.border_threat * 100).toFixed(0) + "%" },
      ];
      envItems.forEach(function (it) {
        var span = h("span", "env-item", "<strong>" + it.label + ":</strong> " + it.value);
        row.appendChild(span);
      });
      envDiv.appendChild(row);
    }

    // Disaster alert
    var alertDiv = el("disaster-alert");
    if (c.disaster_this_year) {
      var d = c.disaster_this_year;
      var dName = DISASTER_NAMES[d.type] || d.type;
      alertDiv.innerHTML =
        "<strong>" + dName + "警报</strong>" +
        "严重程度: " + (d.severity * 100).toFixed(0) + "%" +
        (d.relieved ? " — 已赈灾" : " — <em>尚未赈灾</em>");
      alertDiv.classList.remove("hidden");
    } else {
      alertDiv.classList.add("hidden");
    }

    // Active investments
    var invDiv = el("investments-list");
    invDiv.innerHTML = "";
    if (c.active_investments && c.active_investments.length > 0) {
      var invTitle = h("h4", "section-title", "进行中的投资");
      invDiv.appendChild(invTitle);
      c.active_investments.forEach(function (inv) {
        var label = inv.description;
        if (inv.target_village) label += "（" + inv.target_village + "）";
        var item = h("div", "invest-item",
          "<span>" + label + "</span>" +
          "<span>预计第" + inv.completion_season + "季度完成</span>");
        invDiv.appendChild(item);
      });
    }

    // Markets
    var mkDiv = el("markets-info");
    mkDiv.innerHTML = "";
    if (c.markets && c.markets.length > 0) {
      var mkTitle = h("h4", "section-title", "集市");
      mkDiv.appendChild(mkTitle);
      c.markets.forEach(function (m) {
        var row = h("div", "market-row",
          "<span><strong>" + m.name + "</strong></span>" +
          "<span>商户: " + m.merchants + "</span>" +
          "<span>贸易指数: " + m.trade_index + "</span>");
        mkDiv.appendChild(row);
      });
    }

    // Player profile
    var plDiv = el("player-info");
    plDiv.innerHTML = "";
    if (g.player) {
      var p = g.player;
      var plTitle = h("h4", "section-title", "知县档案");
      plDiv.appendChild(plTitle);
      var card = h("div", "player-card",
        '<div class="player-row">' +
          "<span><strong>出身:</strong> " + p.background_display + "</span>" +
          "<span><strong>知识:</strong> " + p.knowledge.toFixed(1) + "</span>" +
          "<span><strong>技能:</strong> " + p.skill.toFixed(1) + "</span>" +
          "<span><strong>清名:</strong> " + p.integrity + "</span>" +
          "<span><strong>能名:</strong> " + p.competence + "</span>" +
          "<span><strong>人缘:</strong> " + p.popularity + "</span>" +
        "</div>");
      plDiv.appendChild(card);
    }

    // Active promises
    loadActivePromises();
  }

  function loadActivePromises() {
    var g = Game.state.currentGame;
    if (!g) return;
    var container = el("promises-info");
    if (!container) return;

    Game.api.getPromises(g.id, "PENDING")
      .then(function (promises) {
        renderPromises(promises);
      })
      .catch(function () {
        container.innerHTML = "";
      });
  }

  function renderPromises(promises) {
    var container = el("promises-info");
    if (!container) return;
    container.innerHTML = "";

    if (!promises || promises.length === 0) return;

    var title = h("h4", "section-title", "待履行承诺 (" + promises.length + ")");
    container.appendChild(title);

    var list = h("div", "promise-list");
    promises.forEach(function (p) {
      var g = Game.state.currentGame;
      var remaining = p.deadline_season - (g ? g.current_season : 0);
      var urgentClass = remaining <= 1 ? " promise-urgent" : "";
      var item = h("div", "promise-item" + urgentClass,
        '<div class="promise-item-header">' +
          '<span class="promise-type">' + p.promise_type_display + '</span>' +
          '<span class="promise-deadline">截止第' + p.deadline_season + '季' +
            (remaining > 0 ? '（剩余' + remaining + '季）' : '（已到期）') +
          '</span>' +
        '</div>' +
        '<div class="promise-desc">' + escapeHtml(p.description) + '</div>' +
        '<div class="promise-agent">对象: ' + p.agent_name + '</div>');
      list.appendChild(item);
    });
    container.appendChild(list);
  }

  // Default initial villages for games created before initial_villages was stored
  var DEFAULT_INITIAL_VILLAGES = [
    { name: "李家村", population: 1024, farmland: 6400, gentry_land_pct: 0.35, morale: 50, security: 55, has_school: false },
    { name: "张家村", population: 896, farmland: 5200, gentry_land_pct: 0.30, morale: 52, security: 58, has_school: false },
    { name: "王家村", population: 1064, farmland: 7200, gentry_land_pct: 0.40, morale: 48, security: 50, has_school: false },
    { name: "陈家村", population: 813, farmland: 4400, gentry_land_pct: 0.25, morale: 55, security: 60, has_school: false },
    { name: "赵家村", population: 855, farmland: 5600, gentry_land_pct: 0.38, morale: 45, security: 52, has_school: false },
    { name: "刘家村", population: 630, farmland: 3200, gentry_land_pct: 0.20, morale: 53, security: 57, has_school: false },
  ];

  function deltaSpan(current, initial, invert) {
    var diff = current - initial;
    if (Math.abs(diff) < 0.001) return "";
    var sign = diff > 0 ? "+" : "";
    var cls;
    if (invert) {
      cls = diff > 0 ? "delta-negative" : "delta-positive";
    } else {
      cls = diff > 0 ? "delta-positive" : "delta-negative";
    }
    // Format: integers for pop/farmland, 1 decimal for others
    var formatted = Number.isInteger(diff) ? diff : diff.toFixed(1);
    return ' <span class="' + cls + '">(' + sign + formatted + ')</span>';
  }

  function findInitialVillage(name, initialVillages) {
    for (var i = 0; i < initialVillages.length; i++) {
      if (initialVillages[i].name === name) return initialVillages[i];
    }
    return null;
  }

  function renderVillages() {
    var g = Game.state.currentGame;
    if (!g) return;
    var tbody = el("village-tbody");
    tbody.innerHTML = "";

    var initials = g.county_data.initial_villages || DEFAULT_INITIAL_VILLAGES;

    g.county_data.villages.forEach(function (v) {
      var iv = findInitialVillage(v.name, initials);

      var popDelta = iv ? deltaSpan(v.population, iv.population, false) : "";
      var farmDelta = iv ? deltaSpan(v.farmland, iv.farmland, false) : "";
      var gentryDelta = iv ? deltaSpan(
        Math.round(v.gentry_land_pct * 100),
        Math.round(iv.gentry_land_pct * 100),
        true  // higher gentry % is bad
      ) : "";
      var moraleDelta = iv ? deltaSpan(Math.round(v.morale), Math.round(iv.morale), false) : "";
      var securityDelta = iv ? deltaSpan(Math.round(v.security), Math.round(iv.security), false) : "";

      var ceiling = v.ceiling || 0;
      var occupancy = ceiling > 0 ? Math.round(v.population / ceiling * 100) : 0;
      var occClass = occupancy > 90 ? "delta-negative" : (occupancy > 70 ? "" : "delta-positive");

      // Find gentry and village rep agents for this village
      var gentryHtml = "";
      var repHtml = "";
      var agents = Game.state.agents || [];
      for (var ai = 0; ai < agents.length; ai++) {
        var ag = agents[ai];
        if (ag.village_name === v.name) {
          if (ag.role === "GENTRY" && ag.role_title === "地主") {
            gentryHtml = '<span class="agent-link" data-agent-id="' + ag.id + '">' + ag.name + '</span>';
          } else if (ag.role === "VILLAGER" && ag.role_title === "村民代表") {
            repHtml = '<span class="agent-link" data-agent-id="' + ag.id + '">' + ag.name + '</span>';
          }
        }
      }

      var tr = h("tr", "",
        "<td>" + v.name + "</td>" +
        "<td>" + v.population + popDelta + "</td>" +
        "<td>" + ceiling + ' <span class="' + occClass + '">(' + occupancy + '%)</span></td>' +
        "<td>" + v.farmland + farmDelta + "</td>" +
        "<td>" + (v.gentry_land_pct * 100).toFixed(0) + "%" + gentryDelta + "</td>" +
        "<td>" + Math.round(v.morale) + moraleDelta + "</td>" +
        "<td>" + Math.round(v.security) + securityDelta + "</td>" +
        "<td>" + (v.has_school ? "有" : "无") + "</td>" +
        "<td>" + (gentryHtml || "-") + "</td>" +
        "<td>" + (repHtml || "-") + "</td>");
      tbody.appendChild(tr);
    });
  }

  function getDisableReason(action, county) {
    if (county.treasury < getInvestCost(action)) {
      return "资金不足";
    }
    if (action === "build_irrigation" && county.irrigation_level >= 2) {
      return "已达上限";
    }
    if (action === "hire_bailiffs" && county.bailiff_level >= 3) {
      return "已达上限";
    }
    if (action === "build_granary" && county.has_granary) {
      return "已建成";
    }
    if (action === "relief") {
      if (!county.disaster_this_year) return "无灾害";
      if (county.disaster_this_year.relieved) return "已赈灾";
    }
    return null;
  }

  function getInvestCost(action) {
    for (var i = 0; i < INVEST_DEFS.length; i++) {
      if (INVEST_DEFS[i].action === action) return INVEST_DEFS[i].cost;
    }
    return Infinity;
  }

  function renderInvestTab() {
    var g = Game.state.currentGame;
    if (!g) return;
    var c = g.county_data;

    // Sync medical slider
    var medNames = ["无", "简易医馆", "县医署", "完善医疗"];
    var medCosts = [0, 25, 50, 100];
    var ml = c.medical_level || 0;
    var medSlider = el("medical-slider");
    var medDisplay = el("medical-display");
    if (medSlider) {
      medSlider.value = ml;
      medDisplay.textContent = ml + "级 — " + medNames[ml] + "（" + medCosts[ml] + "两/年）";
    }

    var container = el("invest-cards");
    container.innerHTML = "";

    INVEST_DEFS.forEach(function (def) {
      var reason = getDisableReason(def.action, c);
      var isGameOver = g.current_season > 12;
      var disabled = reason !== null || isGameOver;

      var card = h("div", "invest-card" + (disabled ? " disabled" : ""));
      card.innerHTML =
        '<div class="card-name">' + def.name + '</div>' +
        '<div class="card-cost">费用: ' + def.cost + ' 两</div>' +
        '<div class="card-desc">' + def.desc + '</div>' +
        (reason ? '<div class="card-reason">' + reason + '</div>' : '');

      if (!disabled) {
        card.dataset.action = def.action;
        card.dataset.needsVillage = def.needsVillage ? "1" : "0";
      }

      container.appendChild(card);
    });

    // Disable advance button if game is over
    var advBtn = el("btn-advance");
    if (g.current_season > 12) {
      advBtn.disabled = true;
      advBtn.textContent = "任期已结束";
    } else {
      advBtn.disabled = false;
      advBtn.textContent = "推进季度";
    }
  }

  function renderReport(report) {
    var container = el("report-content");
    container.innerHTML = "";

    // Season header
    var header = h("h3", "", Game.seasonName(report.season) + " 季报");
    container.appendChild(header);

    // Events
    if (report.events && report.events.length > 0) {
      var eventsDiv = h("div", "report-events");
      report.events.forEach(function (evt) {
        var isAnnexation = evt.indexOf("【地主兼并】") !== -1;
        var item = h("div", "report-event" + (isAnnexation ? " report-event-urgent" : ""), evt);
        if (isAnnexation) {
          var btn = h("button", "btn btn-primary btn-small report-nego-btn", "前往谈判");
          btn.id = "btn-report-negotiate";
          item.appendChild(btn);
        }
        eventsDiv.appendChild(item);
      });
      container.appendChild(eventsDiv);
    }

    // Population update detail (autumn)
    if (report.population_update) {
      var pu = report.population_update;
      var popSec = h("div", "report-section");
      popSec.appendChild(h("h4", "", "年度人口变化"));
      var popTable = '<table class="data-table" style="font-size:0.85em;margin-bottom:8px;">' +
        '<thead><tr><th>村庄</th><th>人口</th><th>上限</th><th>增长率</th><th>自然增减</th><th>迁入</th><th>流出</th><th>变后</th></tr></thead><tbody>';
      pu.villages.forEach(function (vd) {
        var changeSign = (vd.pop_after - vd.pop_before) >= 0 ? "+" : "";
        popTable += '<tr>' +
          '<td>' + vd.name + '</td>' +
          '<td>' + vd.pop_before + '</td>' +
          '<td>' + vd.ceiling + '</td>' +
          '<td>' + vd.growth_rate + '%</td>' +
          '<td>' + (vd.delta_growth >= 0 ? "+" : "") + vd.delta_growth + '</td>' +
          '<td>+' + vd.inflow + '</td>' +
          '<td>-' + vd.outflow + '</td>' +
          '<td>' + vd.pop_after + ' (' + changeSign + (vd.pop_after - vd.pop_before) + ')</td></tr>';
      });
      popTable += '</tbody></table>';
      popTable += '<div style="font-size:0.85em;color:#5c4a2a;">总计: ' + pu.total_before + ' → ' + pu.total_after +
        ' (' + (pu.total_change >= 0 ? "+" : "") + pu.total_change + ')</div>';
      popSec.innerHTML += popTable;
      container.appendChild(popSec);
    }

    // Autumn settlement
    if (report.autumn) {
      var sec = h("div", "report-section");
      sec.appendChild(h("h4", "", "秋季结算"));
      var a = report.autumn;
      var medCostHtml = a.medical_cost > 0
        ? '<span class="report-detail-item"><strong>医疗开支:</strong> ' + a.medical_cost + '两</span>'
        : '';
      var detail = h("div", "report-detail",
        '<span class="report-detail-item"><strong>农业产出:</strong> ' + a.total_agri_output + '两</span>' +
        '<span class="report-detail-item"><strong>农业税:</strong> ' + a.agri_tax + '两</span>' +
        '<span class="report-detail-item"><strong>商业税:</strong> ' + a.commercial_tax + '两</span>' +
        '<span class="report-detail-item"><strong>总税收:</strong> ' + a.total_tax + '两</span>' +
        '<span class="report-detail-item"><strong>上缴朝廷:</strong> ' + a.remit_to_central + '两</span>' +
        '<span class="report-detail-item"><strong>行政开支:</strong> ' + a.admin_cost + '两</span>' +
        medCostHtml +
        '<span class="report-detail-item"><strong>县库净变化:</strong> ' + a.net_treasury_change + '两</span>' +
        '<span class="report-detail-item"><strong>县库余额:</strong> ' + a.treasury_after + '两</span>');
      sec.appendChild(detail);
      container.appendChild(sec);
    }

    // Winter snapshot
    if (report.winter_snapshot) {
      var wsec = h("div", "report-section");
      wsec.appendChild(h("h4", "", "年终总结（第" + report.winter_snapshot.year + "年）"));
      var w = report.winter_snapshot;
      var wdetail = h("div", "report-detail",
        '<span class="report-detail-item"><strong>总人口:</strong> ' + w.total_population + '</span>' +
        '<span class="report-detail-item"><strong>总耕地:</strong> ' + w.total_farmland + '亩</span>' +
        '<span class="report-detail-item"><strong>县库:</strong> ' + w.treasury + '两</span>' +
        '<span class="report-detail-item"><strong>民心:</strong> ' + w.morale + '</span>' +
        '<span class="report-detail-item"><strong>治安:</strong> ' + w.security + '</span>' +
        '<span class="report-detail-item"><strong>商业:</strong> ' + w.commercial + '</span>' +
        '<span class="report-detail-item"><strong>文教:</strong> ' + w.education + '</span>');
      wsec.appendChild(wdetail);
      container.appendChild(wsec);
    }

    // Game over notice
    if (report.game_over) {
      var notice = h("div", "report-section",
        '<h4>任期结束</h4><p>三年任期已满，点击下方按钮查看总结。</p>');
      container.appendChild(notice);
    }
  }

  function renderGameList(games) {
    var container = el("game-list");
    container.innerHTML = "";

    if (games.length === 0) {
      container.innerHTML = '<p class="hint">暂无存档，请开始新游戏</p>';
      return;
    }

    games.forEach(function (g) {
      var card = h("div", "game-card");
      var seasonText = g.current_season > 12 ? "已结束" : Game.seasonName(g.current_season);
      card.innerHTML =
        '<div class="game-card-info">' +
          '存档 #' + g.id +
          '<span>' + seasonText + '</span>' +
        '</div>';
      var btn = h("button", "btn btn-small", "继续");
      btn.dataset.gameId = g.id;
      btn.className = "btn btn-small btn-continue";
      card.appendChild(btn);
      container.appendChild(card);
    });
  }

  function renderSummary(summary) {
    var statsDiv = el("summary-stats");
    statsDiv.innerHTML = "";
    var grid = h("div", "summary-grid");
    var items = [
      { label: "总人口", value: summary.total_population },
      { label: "总耕地", value: summary.total_farmland + "亩" },
      { label: "县库", value: summary.treasury + "两" },
      { label: "民心", value: summary.morale },
      { label: "治安", value: summary.security },
      { label: "商业", value: summary.commercial },
      { label: "文教", value: summary.education },
      { label: "水利等级", value: summary.irrigation_level + "/2" },
      { label: "衙役等级", value: summary.bailiff_level + "/3" },
    ];
    items.forEach(function (it) {
      var stat = h("div", "summary-stat",
        '<div class="label">' + it.label + '</div>' +
        '<div class="value">' + it.value + '</div>');
      grid.appendChild(stat);
    });
    statsDiv.appendChild(grid);

    // Village table
    var vDiv = el("summary-villages");
    vDiv.innerHTML = "";
    if (summary.villages) {
      var title = h("h3", "section-title", "村庄概况");
      vDiv.appendChild(title);
      var table = h("table", "data-table");
      table.innerHTML =
        "<thead><tr>" +
          "<th>村庄</th><th>人口</th><th>耕地</th><th>村塾</th>" +
        "</tr></thead>";
      var tbody = document.createElement("tbody");
      summary.villages.forEach(function (v) {
        var tr = h("tr", "",
          "<td>" + v.name + "</td>" +
          "<td>" + v.population + "</td>" +
          "<td>" + v.farmland + "</td>" +
          "<td>" + (v.has_school ? "有" : "无") + "</td>");
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      vDiv.appendChild(table);
    }
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

  // ==================== Negotiation ====================

  var EVENT_TYPE_NAMES = {
    ANNEXATION: "地主兼并",
    IRRIGATION: "兴建水利",
  };

  function checkActiveNegotiation() {
    var g = Game.state.currentGame;
    if (!g) return;

    Game.api.getActiveNegotiation(g.id)
      .then(function (data) {
        Game.state.activeNegotiation = data.active ? data.session : null;
        renderNegotiationBanner();
        renderIrrigationNegotiateSection();
      })
      .catch(function () {
        Game.state.activeNegotiation = null;
        renderNegotiationBanner();
        renderIrrigationNegotiateSection();
      });
  }

  function renderNegotiationBanner() {
    var banner = el("negotiation-banner");
    var session = Game.state.activeNegotiation;

    if (!session) {
      banner.classList.add("hidden");
      banner.innerHTML = "";
      // Also remove stale "前往谈判" button in report tab
      var reportBtn = document.getElementById("btn-report-negotiate");
      if (reportBtn) {
        var parent = reportBtn.closest(".report-event-urgent");
        if (parent) {
          parent.classList.remove("report-event-urgent");
          parent.style.fontWeight = "normal";
        }
        reportBtn.remove();
      }
      return;
    }

    var typeName = EVENT_TYPE_NAMES[session.event_type] || session.event_type;
    banner.classList.remove("hidden");
    banner.innerHTML =
      '<div class="nego-banner-text">' +
        '<strong>' + typeName + '谈判进行中</strong> — ' +
        session.agent_name + '（' + session.agent_role_title + '）' +
        ' 第' + session.current_round + '/' + session.max_rounds + '轮' +
      '</div>' +
      '<button class="btn btn-primary btn-small" id="btn-open-negotiation">进入谈判</button>';
  }

  function renderIrrigationNegotiateSection() {
    var section = el("irrigation-negotiate-section");
    var g = Game.state.currentGame;
    if (!g) { section.classList.add("hidden"); return; }

    // Find active irrigation investment
    var irrigationInv = null;
    (g.county_data.active_investments || []).forEach(function (inv) {
      if (inv.action === "build_irrigation") irrigationInv = inv;
    });

    if (!irrigationInv || Game.state.activeNegotiation) {
      section.classList.add("hidden");
      return;
    }

    // Filter out villages already negotiated
    var negotiated = irrigationInv.negotiated_villages || [];
    var remaining = g.county_data.villages.filter(function (v) {
      return negotiated.indexOf(v.name) === -1;
    });

    if (remaining.length === 0) {
      section.classList.add("hidden");
      return;
    }

    section.classList.remove("hidden");
    var html =
      '<h4 class="section-title">水利协商</h4>' +
      '<p style="font-size:0.9em;margin-bottom:10px;color:#5c4a2a;">' +
        '水利工程建设中，可与各村地主协商，请其出资分担费用：</p>' +
      '<div class="irrigation-village-btns">';

    remaining.forEach(function (v) {
      html += '<button class="btn btn-small btn-irrigation-nego" data-village="' +
        v.name + '">' + v.name + '地主</button>';
    });

    html += '</div>';
    section.innerHTML = html;
  }

  function openNegotiationModal(session) {
    var g = Game.state.currentGame;
    if (!g) return;

    var typeName = EVENT_TYPE_NAMES[session.event_type] || session.event_type;
    el("nego-title").textContent = typeName + "谈判";
    el("nego-subtitle").textContent =
      session.agent_name + "（" + session.agent_role_title + "） " +
      "第" + session.current_round + "/" + session.max_rounds + "轮";

    var msgContainer = el("nego-messages");
    msgContainer.innerHTML = '<p class="hint">加载中...</p>';

    var resolvedDiv = el("nego-resolved");
    resolvedDiv.classList.add("hidden");
    resolvedDiv.innerHTML = "";

    var inputArea = el("nego-input-area");

    if (session.status === "resolved") {
      inputArea.classList.add("hidden");
      resolvedDiv.classList.remove("hidden");
      resolvedDiv.innerHTML = '<div class="nego-outcome">谈判已结束</div>';
    } else {
      inputArea.classList.remove("hidden");
    }

    el("negotiation-modal").classList.remove("hidden");
    el("nego-input").value = "";
    el("nego-input").focus();

    // Load history
    Game.api.getNegotiationHistory(g.id, session.id)
      .then(function (data) {
        renderNegotiationMessages(data.messages || []);
        if (data.session) {
          Game.state.activeNegotiation = data.session;
          el("nego-subtitle").textContent =
            data.session.agent_name + "（" + data.session.agent_role_title + "） " +
            "第" + data.session.current_round + "/" + data.session.max_rounds + "轮";
        }
      })
      .catch(function () {
        msgContainer.innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  function renderNegotiationMessages(messages) {
    var container = el("nego-messages");
    container.innerHTML = "";

    if (messages.length === 0) {
      container.innerHTML = '<p class="hint" style="text-align:center;padding:20px;">开始与对方交涉吧</p>';
      return;
    }

    messages.forEach(function (msg) {
      var cls = msg.role === "player" ? "nego-msg nego-msg-player" : "nego-msg nego-msg-agent";
      var label = msg.role === "player" ? "县令" : "";
      var div = h("div", cls,
        (label ? '<span class="nego-msg-label">' + label + '</span>' : '') +
        '<div class="nego-msg-content">' + escapeHtml(msg.content) + '</div>');
      container.appendChild(div);
    });

    container.scrollTop = container.scrollHeight;
  }

  function appendNegotiationMessage(role, content) {
    var container = el("nego-messages");
    // Remove the hint if present
    var hint = container.querySelector(".hint");
    if (hint) hint.remove();

    var cls = role === "player" ? "nego-msg nego-msg-player" : "nego-msg nego-msg-agent";
    var label = role === "player" ? "县令" : "";
    var div = h("div", cls,
      (label ? '<span class="nego-msg-label">' + label + '</span>' : '') +
      '<div class="nego-msg-content">' + escapeHtml(content) + '</div>');
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function showNegotiationResolved(result) {
    var inputArea = el("nego-input-area");
    inputArea.classList.add("hidden");

    var resolvedDiv = el("nego-resolved");
    resolvedDiv.classList.remove("hidden");

    var decision = result.final_decision || "unknown";
    var decisionText = {
      "stop_annexation": "地主同意停止兼并",
      "proceed_annexation": "地主执意继续兼并",
      "accept": "地主同意出资",
      "refuse": "地主拒绝出资",
    }[decision] || decision;

    var extraHtml = "";
    if (result.event_type === "IRRIGATION" && decision === "accept" && result.contribution_offer) {
      extraHtml =
        '<br><span style="color:#6b8f4a;font-weight:bold;">出资: ' +
        result.contribution_offer + ' 两</span>';
      if (result.treasury !== undefined) {
        extraHtml += '<br>县库余额: ' + result.treasury + ' 两';
      }
    }

    resolvedDiv.innerHTML =
      '<div class="nego-outcome">' +
        '<strong>谈判结束</strong><br>' +
        decisionText + extraHtml +
      '</div>';

    el("nego-subtitle").textContent += " [已结束]";
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  var CATEGORY_LABELS = {
    SYSTEM: "系统",
    INVESTMENT: "投资",
    TAX: "税率",
    NEGOTIATION: "谈判",
    DISASTER: "灾害",
    SETTLEMENT: "结算",
    ANNEXATION: "兼并",
    PROMISE: "承诺",
  };

  var CATEGORY_COLORS = {
    SYSTEM: "#8a7a5a",
    INVESTMENT: "#2e86c1",
    TAX: "#8e44ad",
    NEGOTIATION: "#d4a017",
    DISASTER: "#c0392b",
    SETTLEMENT: "#27ae60",
    ANNEXATION: "#e67e22",
    PROMISE: "#8e44ad",
  };

  function renderEventLogs(logs) {
    var container = el("events-list");
    container.innerHTML = "";

    if (!logs || logs.length === 0) {
      container.innerHTML = '<p class="hint">暂无事件记录</p>';
      return;
    }

    logs.forEach(function (log) {
      var catLabel = CATEGORY_LABELS[log.category] || log.category;
      var catColor = CATEGORY_COLORS[log.category] || "#8a7a5a";
      var seasonText = Game.seasonName(log.season);

      var item = h("div", "event-log-item");
      item.innerHTML =
        '<div class="event-log-header">' +
          '<span class="event-log-category" style="background:' + catColor + ';">' + catLabel + '</span>' +
          '<span class="event-log-season">' + seasonText + '</span>' +
        '</div>' +
        '<div class="event-log-desc">' + escapeHtml(log.description || log.event_type) + '</div>';
      container.appendChild(item);
    });
  }

  function renderRelationships(agents) {
    var container = el("relationships-list");
    container.innerHTML = "";

    if (!agents || agents.length === 0) {
      container.innerHTML = '<p class="hint">暂无人脉数据</p>';
      return;
    }

    agents.forEach(function (a) {
      var card = h("div", "relationship-card");

      // Affinity coloring
      var aff = a.affinity;
      var affClass = aff < 30 ? "affinity-low" : (aff < 60 ? "affinity-mid" : "affinity-high");
      var barColor = aff < 30 ? "#c0392b" : (aff < 60 ? "#d4a017" : "#27ae60");

      // Affinity bar: map -99..99 to 0..100%
      var barPct = Math.max(0, Math.min(100, ((aff + 99) / 198) * 100));

      var roleInfo = a.role_title;
      if (a.village_name) roleInfo += "（" + a.village_name + "）";

      var html =
        '<div class="relationship-card-header">' +
          '<span class="relationship-name">' + a.name + '</span>' +
          '<span class="relationship-role">' + roleInfo + '</span>' +
        '</div>' +
        '<div class="affinity-value ' + affClass + '">好感度: ' + aff + '</div>' +
        '<div class="affinity-bar">' +
          '<div class="affinity-bar-fill" style="width:' + barPct.toFixed(1) + '%;background:' + barColor + ';"></div>' +
        '</div>';

      // Recent memories
      if (a.memory && a.memory.length > 0) {
        html += '<div class="memory-list">';
        a.memory.forEach(function (m) {
          html += '<div class="memory-list-item">' + escapeHtml(m) + '</div>';
        });
        html += '</div>';
      }

      card.innerHTML = html;
      container.appendChild(card);
    });
  }

  // === Agent Profile Modal ===
  var PERSONALITY_LABELS = {
    openness: "开放性",
    conscientiousness: "尽责性",
    agreeableness: "宜人性",
  };
  var IDEOLOGY_LABELS = {
    reform_vs_tradition: "革新↔传统",
    people_vs_authority: "民本↔权威",
    pragmatic_vs_idealist: "务实↔理想",
  };

  function openAgentProfile(agent) {
    var modal = el("agent-profile-modal");
    if (!modal) return;

    var body = el("agent-profile-body");
    body.innerHTML = "";

    // Header
    var roleInfo = agent.role_title;
    if (agent.village_name) roleInfo += "（" + agent.village_name + "）";

    var headerHtml =
      '<div class="profile-section">' +
        '<h3>' + agent.name + '</h3>' +
        '<div class="profile-role">' + roleInfo + '</div>' +
        '<div class="profile-bio">' + escapeHtml(agent.bio || "") + '</div>' +
      '</div>';
    body.innerHTML += headerHtml;

    // Stats
    var statsHtml =
      '<div class="profile-section">' +
        '<h4>基础属性</h4>' +
        '<div class="profile-grid">' +
          '<div class="profile-stat"><span class="profile-stat-label">智力</span><span class="profile-stat-value">' + agent.intelligence + '</span></div>' +
          '<div class="profile-stat"><span class="profile-stat-label">魅力</span><span class="profile-stat-value">' + agent.charisma + '</span></div>' +
          '<div class="profile-stat"><span class="profile-stat-label">忠诚</span><span class="profile-stat-value">' + agent.loyalty + '</span></div>' +
          '<div class="profile-stat"><span class="profile-stat-label">好感度</span><span class="profile-stat-value">' + agent.affinity + '</span></div>' +
        '</div>' +
      '</div>';
    body.innerHTML += statsHtml;

    // Personality
    var p = agent.personality || {};
    var pHtml = '<div class="profile-section"><h4>性格特征</h4><div class="profile-grid">';
    for (var pk in PERSONALITY_LABELS) {
      if (p[pk] !== undefined) {
        pHtml += '<div class="profile-stat"><span class="profile-stat-label">' + PERSONALITY_LABELS[pk] + '</span><span class="profile-stat-value">' + (p[pk] * 100).toFixed(0) + '%</span></div>';
      }
    }
    pHtml += '</div></div>';
    body.innerHTML += pHtml;

    // Ideology
    var ideo = agent.ideology || {};
    var iHtml = '<div class="profile-section"><h4>意识形态</h4><div class="profile-grid">';
    for (var ik in IDEOLOGY_LABELS) {
      if (ideo[ik] !== undefined) {
        iHtml += '<div class="profile-stat"><span class="profile-stat-label">' + IDEOLOGY_LABELS[ik] + '</span><span class="profile-stat-value">' + (ideo[ik] * 100).toFixed(0) + '%</span></div>';
      }
    }
    iHtml += '</div></div>';
    body.innerHTML += iHtml;

    // Reputation
    var rep = agent.reputation || {};
    var rHtml = '<div class="profile-section"><h4>社会声望</h4><div class="profile-grid">';
    if (rep.scholarly !== undefined) rHtml += '<div class="profile-stat"><span class="profile-stat-label">学识</span><span class="profile-stat-value">' + rep.scholarly + '</span></div>';
    if (rep.political !== undefined) rHtml += '<div class="profile-stat"><span class="profile-stat-label">官场</span><span class="profile-stat-value">' + rep.political + '</span></div>';
    if (rep.popular !== undefined) rHtml += '<div class="profile-stat"><span class="profile-stat-label">民间</span><span class="profile-stat-value">' + rep.popular + '</span></div>';
    rHtml += '</div></div>';
    body.innerHTML += rHtml;

    // Goals
    var goals = agent.goals || [];
    if (goals.length > 0) {
      var gHtml = '<div class="profile-section"><h4>目标</h4><ul class="profile-goals">';
      goals.forEach(function (g) { gHtml += '<li>' + escapeHtml(g) + '</li>'; });
      gHtml += '</ul></div>';
      body.innerHTML += gHtml;
    }

    // Backstory
    if (agent.backstory) {
      body.innerHTML += '<div class="profile-section"><h4>背景故事</h4><p class="profile-backstory">' + escapeHtml(agent.backstory) + '</p></div>';
    }

    // Memory
    var mem = agent.all_memory || [];
    if (mem.length > 0) {
      var mHtml = '<div class="profile-section"><h4>记忆 (' + mem.length + ')</h4><div class="profile-memory">';
      mem.forEach(function (m) { mHtml += '<div class="profile-memory-item">' + escapeHtml(m) + '</div>'; });
      mHtml += '</div></div>';
      body.innerHTML += mHtml;
    }

    modal.classList.remove("hidden");
  }

  window.Game.components = {
    renderHeader: renderHeader,
    renderDashboard: renderDashboard,
    renderVillages: renderVillages,
    renderInvestTab: renderInvestTab,
    renderReport: renderReport,
    renderGameList: renderGameList,
    renderSummary: renderSummary,
    showToast: showToast,
    checkActiveNegotiation: checkActiveNegotiation,
    renderNegotiationBanner: renderNegotiationBanner,
    renderIrrigationNegotiateSection: renderIrrigationNegotiateSection,
    openNegotiationModal: openNegotiationModal,
    appendNegotiationMessage: appendNegotiationMessage,
    showNegotiationResolved: showNegotiationResolved,
    renderEventLogs: renderEventLogs,
    renderPromises: renderPromises,
    loadActivePromises: loadActivePromises,
    renderRelationships: renderRelationships,
    openAgentProfile: openAgentProfile,
    INVEST_DEFS: INVEST_DEFS,
  };
})();
