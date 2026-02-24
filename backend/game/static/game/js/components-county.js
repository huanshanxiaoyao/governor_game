/**
 * County management components — Game.components namespace (partial)
 * Depends on: components-core.js
 */
(function () {
  "use strict";

  var C = Game.components;
  var el = C.el, h = C.h, escapeHtml = C.escapeHtml;
  var DISASTER_NAMES = C.DISASTER_NAMES;
  var INVEST_DEFS = C.INVEST_DEFS;
  var INFRA_MAX_LEVEL = C.INFRA_MAX_LEVEL;

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

    // Update commercial tax slider
    var cSlider = el("commercial-tax-slider");
    var cDisplay = el("commercial-tax-display");
    if (cSlider && g.county_data.commercial_tax_rate !== undefined) {
      var cRate = (g.county_data.commercial_tax_rate * 100).toFixed(1);
      // Remove trailing .0
      if (cRate.endsWith(".0")) cRate = cRate.slice(0, -2);
      cSlider.value = cRate;
      cDisplay.textContent = cRate + "%";
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
    var pi = c.price_index || 1.0;
    var ctr = c.commercial_tax_rate !== undefined ? c.commercial_tax_rate : 0.03;
    var items = [
      { label: "税率", value: Math.round(c.tax_rate * 100) + "%" },
      { label: "商税税率", value: (ctr * 100).toFixed(1).replace(/\.0$/, "") + "%" },
      { label: "物价指数", value: pi.toFixed(1) },
      { label: "县学", value: (c.school_level || 0) + "/" + INFRA_MAX_LEVEL },
      { label: "水利", value: (c.irrigation_level || 0) + "/" + INFRA_MAX_LEVEL },
      { label: "医疗", value: (c.medical_level || 0) + "/" + INFRA_MAX_LEVEL },
      { label: "衙役", value: (c.bailiff_level || 0) + "/3" },
      { label: "义仓", value: c.has_granary ? "已建" : "未建" },
    ];
    items.forEach(function (it) {
      var span = h("span", "info-item", "<strong>" + it.label + ":</strong> " + it.value);
      info.appendChild(span);
    });

    // 行政开支（可展开明细）
    var adminSpan = h("span", "info-item info-item-expandable");
    adminSpan.innerHTML = "<strong>行政开支:</strong> " + c.admin_cost + "两/年 ▾";
    adminSpan.style.cursor = "pointer";
    var detailDiv = h("div", "admin-cost-detail");
    detailDiv.style.display = "none";
    if (c.admin_cost_detail) {
      var LABELS = Game.components.ADMIN_COST_LABELS;
      var keys = ["official_salary", "deputy_salary", "advisor_fee", "clerks_cost",
                  "bailiff_cost", "school_cost", "office_cost",
                  "irrigation_maint", "medical_maint"];
      keys.forEach(function (k) {
        var val = c.admin_cost_detail[k];
        if (val !== undefined) {
          var row = h("div", "admin-detail-row",
            "<span class='admin-detail-label'>" + (LABELS[k] || k) + "</span>" +
            "<span class='admin-detail-value'>" + val + "两</span>");
          detailDiv.appendChild(row);
        }
      });
      var totalRow = h("div", "admin-detail-row admin-detail-total",
        "<span class='admin-detail-label'><strong>合计</strong></span>" +
        "<span class='admin-detail-value'><strong>" + c.admin_cost + "两</strong></span>");
      detailDiv.appendChild(totalRow);
    }
    adminSpan.addEventListener("click", function () {
      var showing = detailDiv.style.display !== "none";
      detailDiv.style.display = showing ? "none" : "block";
      adminSpan.innerHTML = "<strong>行政开支:</strong> " + c.admin_cost + "两/年 " + (showing ? "▾" : "▴");
    });
    info.appendChild(adminSpan);
    info.appendChild(detailDiv);

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

    // Peasant surplus info
    var surplusDiv = el("surplus-info");
    if (surplusDiv) {
      surplusDiv.innerHTML = "";
      if (c.peasant_surplus) {
        var ps = c.peasant_surplus;
        var surplusTitle = h("h4", "section-title", "农民粮食盈余");
        surplusDiv.appendChild(surplusTitle);
        var surplusRow = h("div", "env-row");
        var monthlyPcs = ps.monthly_per_capita_surplus || 0;
        var trendIcon = monthlyPcs >= 10 ? "↑" : (monthlyPcs <= 0 ? "↓" : "→");
        var trendClass = monthlyPcs >= 10 ? "delta-positive" : (monthlyPcs <= 0 ? "delta-negative" : "");
        var demandInfo = ps.demand_factor !== undefined ? "（需求系数: " + ps.demand_factor + "）" : "";
        var surplusItems = [
          { label: "当前储备", value: ps.reserve.toLocaleString() + "斤" },
          { label: "月消耗", value: ps.monthly_consumption.toLocaleString() + "斤" },
          { label: "距秋收", value: ps.months_to_harvest + "月" },
          { label: "月均余粮", value: '<span class="' + trendClass + '">' + monthlyPcs + "斤 " + trendIcon + "</span>" + demandInfo },
        ];
        surplusItems.forEach(function (it) {
          var span = h("span", "env-item", "<strong>" + it.label + ":</strong> " + it.value);
          surplusRow.appendChild(span);
        });
        surplusDiv.appendChild(surplusRow);
      }
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
          "<span>预计" + (inv.completion_season <= Game.MAX_MONTH ? Game.seasonName(inv.completion_season).split("（")[0] : "任期后") + "完成</span>");
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
        var gmvVal = m.gmv !== undefined ? m.gmv : (m.trade_index || 0);
        var row = h("div", "market-row",
          "<span><strong>" + m.name + "</strong></span>" +
          "<span>商户: " + m.merchants + "</span>" +
          "<span>月贸易额: " + gmvVal + "两</span>");
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
          '<span class="promise-deadline">截止第' + p.deadline_season + '月' +
            (remaining > 0 ? '（剩余' + remaining + '月）' : '（已到期）') +
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

  function renderInvestTab() {
    var g = Game.state.currentGame;
    if (!g) return;

    var container = el("invest-cards");
    container.innerHTML = "";

    // Use backend-provided available_investments (pre-calculated costs and disable reasons)
    var actions = g.available_investments || [];
    // Build a lookup from INVEST_DEFS for display descriptions
    var defMap = {};
    INVEST_DEFS.forEach(function (def) { defMap[def.action] = def; });

    actions.forEach(function (item) {
      var def = defMap[item.action] || {};
      var reason = item.disabled_reason || null;
      var isGameOver = g.current_season > Game.MAX_MONTH;
      var disabled = reason !== null || isGameOver;

      var levelInfo = "";
      if (item.current_level !== null && item.max_level !== null) {
        levelInfo = " (当前" + item.current_level + "/" + item.max_level + "级)";
      }

      var card = h("div", "invest-card" + (disabled ? " disabled" : ""));
      card.innerHTML =
        '<div class="card-name">' + item.name + levelInfo + '</div>' +
        '<div class="card-cost">费用: ' + item.cost + ' 两</div>' +
        '<div class="card-desc">' + (def.desc || "") + '</div>' +
        (reason ? '<div class="card-reason">' + reason + '</div>' : '');

      if (!disabled) {
        card.dataset.action = item.action;
        card.dataset.needsVillage = item.requires_village ? "1" : "0";
      }

      container.appendChild(card);
    });

    // Disable advance button if game is over
    var advBtn = el("btn-advance");
    if (g.current_season > Game.MAX_MONTH) {
      advBtn.disabled = true;
      advBtn.textContent = "任期已结束";
    } else {
      advBtn.disabled = false;
      advBtn.textContent = "推进月份";
    }
  }

  function renderReport(report) {
    var container = el("report-content");
    container.innerHTML = "";

    // Season header
    var header = h("h3", "", Game.seasonName(report.season) + " 月报");
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

      // Temporary debug output for Sep migration pairwise calculations
      if (report.autumn && pu.migration && pu.migration.pairs && pu.migration.pairs.length > 0) {
        var mig = pu.migration;
        var pairs = pu.migration.pairs;

        var fmtSigned = function (n) {
          if (n === undefined || n === null || isNaN(Number(n))) return "-";
          var num = Number(n);
          return (num > 0 ? "+" : "") + num.toFixed(1);
        };

        var bucketLabel = function (bucket) {
          if (bucket === "lead") return "领先";
          if (bucket === "lag") return "落后";
          if (bucket === "parity") return "持平";
          return "中间";
        };

        var decisionLabel = function (direction) {
          if (direction === "inflow") return "触发流入";
          if (direction === "outflow") return "触发流出";
          return "未触发";
        };

        var migrationSigned = function (direction, moved) {
          if (!moved) return "0";
          if (direction === "outflow") return "-" + moved;
          return "+" + moved;
        };

        var pairTable = '<div class="report-debug-note">【临时调试信息】邻县两两对比中间结果（短期测试用，后续请删除）</div>';
        pairTable += '<table class="data-table report-debug-table">' +
          '<thead><tr>' +
          '<th>邻县</th><th>民心Δ</th><th>治安Δ</th><th>商业Δ</th><th>文教Δ</th>' +
          '<th>计数(领/落/平/中)</th><th>判定</th><th>迁移率</th><th>迁移人数</th>' +
          '</tr></thead><tbody>';

        pairs.forEach(function (pair) {
          var dims = pair.dim_details || {};
          var dm = dims.morale || {};
          var ds = dims.security || {};
          var dc = dims.commercial || {};
          var de = dims.education || {};
          var rate = pair.rate ? (pair.rate * 100).toFixed(1) + "%" : "-";

          pairTable += '<tr>' +
            '<td>' + escapeHtml(pair.peer_name || ("邻县" + (pair.peer_index || ""))) + '</td>' +
            '<td>' + fmtSigned(dm.diff) + ' (' + bucketLabel(dm.bucket) + ')</td>' +
            '<td>' + fmtSigned(ds.diff) + ' (' + bucketLabel(ds.bucket) + ')</td>' +
            '<td>' + fmtSigned(dc.diff) + ' (' + bucketLabel(dc.bucket) + ')</td>' +
            '<td>' + fmtSigned(de.diff) + ' (' + bucketLabel(de.bucket) + ')</td>' +
            '<td>' + (pair.lead_count || 0) + '/' + (pair.lag_count || 0) + '/' +
              (pair.parity_count || 0) + '/' + (pair.mid_count || 0) + '</td>' +
            '<td>' + decisionLabel(pair.direction) + '</td>' +
            '<td>' + rate + '</td>' +
            '<td>' + migrationSigned(pair.direction, pair.moved || 0) + '</td>' +
            '</tr>';
        });

        pairTable += '</tbody></table>';
        pairTable += '<div class="report-debug-note">迁移汇总: 流入' + (mig.inflow_total || 0) + ' / 流出' + (mig.outflow_total || 0) +
          ' / 单方向上限' + (mig.cap_rate !== undefined ? (mig.cap_rate * 100).toFixed(1) + '%' : '-') + '</div>';
        popTable += pairTable;
      }

      popSec.innerHTML += popTable;
      container.appendChild(popSec);
    }

    // Autumn settlement
    if (report.autumn) {
      var sec = h("div", "report-section");
      sec.appendChild(h("h4", "", "秋季结算"));
      var a = report.autumn;
      // Support both old format (corvee_tax) and new format (corvee_tax_ytd)
      var corveeHtml = a.corvee_tax_ytd !== undefined
        ? '<span class="report-detail-item"><strong>年度徭役(已征):</strong> ' + a.corvee_tax_ytd + '两(留存' + a.corvee_retained_ytd + '两)</span>'
        : '';
      var commercialHtml = a.commercial_tax_ytd !== undefined
        ? '<span class="report-detail-item"><strong>年度商税(已征):</strong> ' + a.commercial_tax_ytd + '两(留存' + a.commercial_retained_ytd + '两)</span>'
        : '<span class="report-detail-item"><strong>商业税:</strong> ' + (a.commercial_tax || 0) + '两</span>';
      var detail = h("div", "report-detail",
        '<span class="report-detail-item"><strong>农业产出:</strong> ' + a.total_agri_output + '两</span>' +
        '<span class="report-detail-item"><strong>农业税:</strong> ' + a.agri_tax + '两</span>' +
        corveeHtml +
        commercialHtml +
        '<span class="report-detail-item"><strong>总税收:</strong> ' + a.total_tax + '两</span>' +
        '<span class="report-detail-item"><strong>总上缴:</strong> ' + a.remit_to_central + '两</span>' +
        '<span class="report-detail-item"><strong>行政开支:</strong> ' + a.admin_cost + '两(含基建维护)</span>' +
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
      var seasonText = g.current_season > Game.MAX_MONTH ? "已结束" : Game.seasonName(g.current_season);
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
    var v2Div = el("summary-v2");
    if (v2Div) v2Div.innerHTML = "";

    var statsDiv = el("summary-stats");
    statsDiv.classList.remove("hidden");
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
      { label: "县学等级", value: (summary.school_level || 0) + "/" + INFRA_MAX_LEVEL },
      { label: "水利等级", value: (summary.irrigation_level || 0) + "/" + INFRA_MAX_LEVEL },
      { label: "医疗等级", value: (summary.medical_level || 0) + "/" + INFRA_MAX_LEVEL },
      { label: "衙役等级", value: (summary.bailiff_level || 0) + "/3" },
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
    vDiv.classList.remove("hidden");
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

  function signedClass(val) {
    if (val > 0) return "delta-positive";
    if (val < 0) return "delta-negative";
    return "delta-neutral";
  }

  function fmtSigned(val, digits) {
    if (val === null || val === undefined || isNaN(val)) return "-";
    var n = Number(val);
    var sign = n > 0 ? "+" : "";
    var text = digits === undefined ? String(n) : n.toFixed(digits);
    return sign + text;
  }

  function renderSummaryV2(data) {
    var root = el("summary-v2");
    if (!root) return;
    root.innerHTML = "";

    var statsDiv = el("summary-stats");
    var villagesDiv = el("summary-villages");
    if (statsDiv) {
      statsDiv.classList.add("hidden");
      statsDiv.innerHTML = "";
    }
    if (villagesDiv) {
      villagesDiv.classList.add("hidden");
      villagesDiv.innerHTML = "";
    }

    var head = data.headline || {};
    var meta = data.meta || {};
    var badges = head.badges || [];
    var tags = head.style_tags || [];

    function fmtMaybe(val, digits) {
      if (val === null || val === undefined || isNaN(val)) return "-";
      var n = Number(val);
      if (digits === undefined) return String(n);
      return n.toFixed(digits);
    }

    var hero = h("section", "summary2-hero",
      '<div class="summary2-title">任期述职报告</div>' +
      '<h2>' + escapeHtml(head.title || "三年任期总结") + '</h2>' +
      '<div class="summary2-score-row">' +
        '<span class="summary2-grade">评级：' + escapeHtml(head.grade || "-") + '</span>' +
        '<span class="summary2-outcome">结论：' + escapeHtml(head.outcome || "-") + '</span>' +
        '<span class="summary2-score">综合分：' + (head.overall_score !== undefined ? head.overall_score : "-") + '</span>' +
      '</div>' +
      '<p class="summary2-narrative">' + escapeHtml(head.narrative || "") + '</p>' +
      '<div class="summary2-chip-row">' +
        tags.map(function (t) { return '<span class="summary2-chip">' + escapeHtml(t) + '</span>'; }).join("") +
      '</div>' +
      '<div class="summary2-chip-row">' +
        badges.map(function (b) { return '<span class="summary2-chip summary2-chip-badge">' + escapeHtml(b) + '</span>'; }).join("") +
      '</div>' +
      '<div class="summary2-baseline">' + escapeHtml(meta.baseline_note || "") + '</div>');
    root.appendChild(hero);

    var kpiSec = h("section", "summary2-section");
    kpiSec.appendChild(h("h3", "section-title", "核心指标"));
    var kpiGrid = h("div", "summary2-kpi-grid");
    (data.kpi_cards || []).forEach(function (k) {
      var finalVal = k.final;
      if (k.unit) finalVal = finalVal + k.unit;
      var deltaText = "-";
      var deltaClass = "delta-neutral";
      if (k.delta !== undefined && k.delta !== null) {
        deltaText = fmtSigned(k.delta, Math.abs(k.delta) < 10 ? 1 : 0) + (k.unit || "");
        deltaClass = signedClass(k.delta);
      } else if (k.delta_pct !== undefined && k.delta_pct !== null) {
        deltaText = fmtSigned(k.delta_pct, 1) + "%";
        deltaClass = signedClass(k.delta_pct);
      }
      var initText = (k.initial !== undefined && k.initial !== null) ? k.initial + (k.unit || "") : "-";
      var card = h("div", "summary2-kpi-card",
        '<div class="summary2-kpi-label">' + escapeHtml(k.label || "") + '</div>' +
        '<div class="summary2-kpi-final">' + escapeHtml(String(finalVal)) + '</div>' +
        '<div class="summary2-kpi-sub">基线: ' + escapeHtml(String(initText)) + '</div>' +
        '<div class="summary2-kpi-delta ' + deltaClass + '">变化: ' + escapeHtml(deltaText) + '</div>');
      kpiGrid.appendChild(card);
    });
    kpiSec.appendChild(kpiGrid);
    root.appendChild(kpiSec);

    var horizontalRows = data.horizontal_benchmark || [];
    var governorScoreRows = data.governor_score_benchmark || [];
    if (horizontalRows.length > 0) {
      var horizontalSec = h("section", "summary2-section");
      horizontalSec.appendChild(h("h3", "section-title", "横向对比（同任期邻县）"));
      var horizontalTable = h("table", "data-table summary2-trend-table");
      horizontalTable.innerHTML =
        "<thead><tr>" +
        "<th>指标</th><th>本县任内变化</th><th>邻县中位变化</th>" +
        "<th>排名</th><th>分位</th>" +
        "</tr></thead>";
      var horizontalBody = document.createElement("tbody");
      horizontalRows.forEach(function (row) {
        var unit = row.unit || "";
        var playerVal = row.player_term_value;
        var peerVal = row.peer_median_term_value;
        var playerText = (playerVal === null || playerVal === undefined) ? "-" : (fmtSigned(playerVal, 1) + unit);
        var peerText = (peerVal === null || peerVal === undefined) ? "-" : (fmtSigned(peerVal, 1) + unit);
        var rankText = "-";
        if (row.rank !== null && row.rank !== undefined && row.total_count) {
          rankText = String(row.rank) + "/" + String(row.total_count);
        }
        var percentileText = row.percentile === null || row.percentile === undefined
          ? "-"
          : fmtMaybe(row.percentile, 1) + "%";
        var tr = h("tr", "",
          "<td>" + escapeHtml(row.label || "") + "</td>" +
          "<td>" + escapeHtml(playerText) + "</td>" +
          "<td>" + escapeHtml(peerText) + "</td>" +
          "<td>" + escapeHtml(rankText) + "</td>" +
          "<td>" + escapeHtml(percentileText) + "</td>");
        horizontalBody.appendChild(tr);
      });
      horizontalTable.appendChild(horizontalBody);
      horizontalSec.appendChild(horizontalTable);
      horizontalSec.appendChild(
        h("p", "summary2-baseline", escapeHtml(meta.horizontal_note || "横向分位越高，说明同周期相对表现越好。"))
      );

      if (governorScoreRows.length > 0) {
        horizontalSec.appendChild(h("h3", "section-title", "邻县知县综合打分"));
        var scoreTable = h("table", "data-table summary2-trend-table");
        scoreTable.innerHTML =
          "<thead><tr>" +
          "<th>县名</th><th>知县</th><th>综合分</th><th>评级</th><th>排名</th><th>任期报告</th>" +
          "</tr></thead>";
        var scoreBody = document.createElement("tbody");
        governorScoreRows.forEach(function (row) {
          var rankText = "-";
          if (row.rank !== null && row.rank !== undefined && row.total_count) {
            rankText = String(row.rank) + "/" + String(row.total_count);
          }
          var tr = h("tr", "",
            "<td>" + escapeHtml(row.county_name || "") + "</td>" +
            "<td>" + escapeHtml((row.governor_name || "") + " 知县") + "</td>" +
            "<td>" + escapeHtml(fmtMaybe(row.comprehensive_score, 1)) + "</td>" +
            "<td>" + escapeHtml(row.grade || "-") + "</td>" +
            "<td>" + escapeHtml(rankText) + "</td>" +
            "<td>" +
              '<button class="btn btn-small summary2-neighbor-report-btn" ' +
                'data-game-id="' + escapeHtml(String(meta.game_id || "")) + '" ' +
                'data-neighbor-id="' + escapeHtml(String(row.neighbor_id || "")) + '">' +
                "查看任期报告" +
              "</button>" +
            "</td>");
          scoreBody.appendChild(tr);
        });
        scoreTable.appendChild(scoreBody);
        horizontalSec.appendChild(scoreTable);
      }
      root.appendChild(horizontalSec);
    }

    var disasterAdj = data.disaster_adjustment || null;
    if (disasterAdj) {
      var dSec = h("section", "summary2-section");
      dSec.appendChild(h("h3", "section-title", "灾害校正"));
      var dText = "";
      if ((disasterAdj.disaster_count || 0) > 0) {
        dText =
          "暴露差值 " + fmtSigned(disasterAdj.exposure_gap, 3) +
          "（本县 " + fmtMaybe(disasterAdj.player_exposure, 3) +
          "，邻县均值 " + fmtMaybe(disasterAdj.peer_avg_exposure, 3) +
          "），暴露消偏 " + fmtSigned(disasterAdj.exposure_offset, 1) +
          "，合计校正 " + fmtSigned(disasterAdj.total_correction, 1) + "。";
      } else {
        dText = "任内无灾害事件，灾害校正项为0。";
      }
      dSec.appendChild(h("p", "summary2-narrative", dText));

      var dGrid = h("div", "summary2-kpi-grid");
      dGrid.appendChild(h("div", "summary2-kpi-card",
        '<div class="summary2-kpi-label">灾害次数</div>' +
        '<div class="summary2-kpi-final">' + escapeHtml(String(disasterAdj.disaster_count || 0)) + '</div>'
      ));
      dGrid.appendChild(h("div", "summary2-kpi-card",
        '<div class="summary2-kpi-label">暴露强度</div>' +
        '<div class="summary2-kpi-final">' + escapeHtml(fmtMaybe(disasterAdj.player_exposure, 3)) + '</div>' +
        '<div class="summary2-kpi-sub">邻县均值: ' + escapeHtml(fmtMaybe(disasterAdj.peer_avg_exposure, 3)) + '</div>'
      ));
      dGrid.appendChild(h("div", "summary2-kpi-card",
        '<div class="summary2-kpi-label">暴露差值</div>' +
        '<div class="summary2-kpi-final">' + escapeHtml(fmtSigned(disasterAdj.exposure_gap, 3)) + '</div>' +
        '<div class="summary2-kpi-sub">消偏: ' + escapeHtml(fmtSigned(disasterAdj.exposure_offset, 1)) + '</div>'
      ));
      dSec.appendChild(dGrid);
      dSec.appendChild(
        h("p", "summary2-baseline", escapeHtml(meta.disaster_note || "灾害多寡存在不可控因素，校正仅用于补偿相对更高的灾害暴露。"))
      );
      root.appendChild(dSec);
    }

    var yearSec = h("section", "summary2-section");
    yearSec.appendChild(h("h3", "section-title", "年度复盘"));
    var yearWrap = h("div", "summary2-year-wrap");
    (data.yearly_reports || []).forEach(function (yr) {
      var w = yr.winter_snapshot || {};
      var a = yr.autumn || {};
      var events = (yr.key_events || []).slice(0, 4);
      var eventHtml = events.map(function (e) {
        return '<li>第' + e.season + '月 [' + escapeHtml(e.category) + '] ' + escapeHtml(e.description) + '</li>';
      }).join("");
      var card = h("article", "summary2-year-card",
        '<div class="summary2-year-header">第' + yr.year + '年</div>' +
        '<div class="summary2-year-metrics">' +
          '<span>县库: ' + (w.treasury !== undefined ? w.treasury : "-") + '两</span>' +
          '<span>民心: ' + (w.morale !== undefined ? w.morale : "-") + '</span>' +
          '<span>治安: ' + (w.security !== undefined ? w.security : "-") + '</span>' +
          '<span>秋收总税: ' + (a.total_tax !== undefined ? a.total_tax : "-") + '两</span>' +
        '</div>' +
        '<p class="summary2-year-summary">' + escapeHtml(yr.summary_text || "") + '</p>' +
        '<ul class="summary2-year-events">' + eventHtml + '</ul>');
      yearWrap.appendChild(card);
    });
    yearSec.appendChild(yearWrap);
    root.appendChild(yearSec);

    var panelSec = h("section", "summary2-section");
    panelSec.appendChild(h("h3", "section-title", "述职要点"));
    var panelGrid = h("div", "summary2-panel-grid");

    var hi = h("div", "summary2-panel");
    hi.appendChild(h("h4", "", "亮点"));
    var hiList = h("ul", "summary2-list");
    (data.highlights || []).forEach(function (item) {
      hiList.appendChild(h("li", "", '<strong>' + escapeHtml(item.title || "") + '：</strong>' + escapeHtml(item.detail || "")));
    });
    hi.appendChild(hiList);
    panelGrid.appendChild(hi);

    var risk = h("div", "summary2-panel");
    risk.appendChild(h("h4", "", "风险"));
    var riskList = h("ul", "summary2-list");
    (data.risks || []).forEach(function (item) {
      riskList.appendChild(h("li", "", '<strong>' + escapeHtml(item.title || "") + '：</strong>' + escapeHtml(item.detail || "")));
    });
    risk.appendChild(riskList);
    panelGrid.appendChild(risk);
    panelSec.appendChild(panelGrid);
    root.appendChild(panelSec);

    var reviewSec = h("section", "summary2-section");
    reviewSec.appendChild(h("h3", "section-title", "多方评价"));
    var reviews = h("div", "summary2-review-list");
    (data.peer_reviews || []).forEach(function (r) {
      reviews.appendChild(h("div", "summary2-review-item",
        '<span class="summary2-review-role">' + escapeHtml(r.role || "") + '：</span>' +
        '<span>' + escapeHtml(r.comment || "") + '</span>'));
    });
    reviewSec.appendChild(reviews);
    root.appendChild(reviewSec);

    // Monthly trend overview (趋势概览)
    var trends = data.monthly_trends || [];
    if (trends.length > 0) {
      var trendSec = h("section", "summary2-section");
      trendSec.appendChild(h("h3", "section-title", "趋势概览"));

      var trendTable = h("table", "data-table summary2-trend-table");
      trendTable.innerHTML =
        "<thead><tr>" +
        "<th>年度</th><th>县库(两)</th><th>人口</th><th>民心</th>" +
        "<th>治安</th><th>商业</th><th>文教</th><th>月GMV(两)</th>" +
        "</tr></thead>";
      var trendBody = document.createElement("tbody");

      // Group by year (12 months each), show year-end (month 12) value with min/max
      for (var yr = 1; yr <= 3; yr++) {
        var yearTrends = trends.filter(function (t) {
          var s = t.season;
          return s >= (yr - 1) * 12 + 1 && s <= yr * 12;
        });
        if (yearTrends.length === 0) continue;

        var yearEnd = yearTrends[yearTrends.length - 1];

        function minMax(arr, key) {
          var vals = arr.map(function (t) { return t[key]; }).filter(function (v) { return v !== undefined && v !== null; });
          if (vals.length === 0) return "";
          var mn = Math.min.apply(null, vals);
          var mx = Math.max.apply(null, vals);
          if (mn === mx) return "";
          return " <small style='color:#888;'>(" + Math.round(mn) + "~" + Math.round(mx) + ")</small>";
        }

        var trendTr = h("tr", "",
          "<td>第" + yr + "年</td>" +
          "<td>" + Math.round(yearEnd.treasury) + minMax(yearTrends, "treasury") + "</td>" +
          "<td>" + yearEnd.total_population + minMax(yearTrends, "total_population") + "</td>" +
          "<td>" + Math.round(yearEnd.morale) + minMax(yearTrends, "morale") + "</td>" +
          "<td>" + Math.round(yearEnd.security) + minMax(yearTrends, "security") + "</td>" +
          "<td>" + Math.round(yearEnd.commercial) + minMax(yearTrends, "commercial") + "</td>" +
          "<td>" + Math.round(yearEnd.education) + minMax(yearTrends, "education") + "</td>" +
          "<td>" + (yearEnd.total_gmv !== undefined ? Math.round(yearEnd.total_gmv) : "-") +
            minMax(yearTrends, "total_gmv") + "</td>");
        trendBody.appendChild(trendTr);
      }

      trendTable.appendChild(trendBody);
      trendSec.appendChild(trendTable);

      var trendNote = h("p", "summary2-baseline", "年末值（括号内为该年度min~max区间），数据来自每月结算快照。");
      trendSec.appendChild(trendNote);
      root.appendChild(trendSec);
    }

    var villageSec = h("section", "summary2-section");
    villageSec.appendChild(h("h3", "section-title", "村庄变化"));
    var table = h("table", "data-table summary2-village-table");
    table.innerHTML = "<thead><tr>" +
      "<th>村庄</th><th>人口</th><th>人口变化</th><th>耕地</th><th>耕地变化</th><th>地主占比</th><th>村塾</th>" +
      "</tr></thead>";
    var tbody = document.createElement("tbody");
    (data.villages || []).forEach(function (v) {
      var popDelta = v.population_delta;
      var farmDelta = v.farmland_delta;
      var popDeltaText = popDelta === null || popDelta === undefined ? "-" : fmtSigned(popDelta, 0);
      var farmDeltaText = farmDelta === null || farmDelta === undefined ? "-" : fmtSigned(farmDelta, 0);
      var gentryPct = (Number(v.gentry_land_pct || 0) * 100).toFixed(1) + "%";
      var tr = h("tr", "",
        "<td>" + escapeHtml(v.name) + "</td>" +
        "<td>" + v.population + "</td>" +
        '<td class="' + signedClass(popDelta || 0) + '">' + popDeltaText + "</td>" +
        "<td>" + v.farmland + "</td>" +
        '<td class="' + signedClass(farmDelta || 0) + '">' + farmDeltaText + "</td>" +
        "<td>" + gentryPct + "</td>" +
        "<td>" + (v.has_school ? "有" : "无") + "</td>");
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    villageSec.appendChild(table);
    root.appendChild(villageSec);
  }

  // Export
  C.renderHeader = renderHeader;
  C.renderDashboard = renderDashboard;
  C.loadActivePromises = loadActivePromises;
  C.renderPromises = renderPromises;
  C.renderVillages = renderVillages;
  C.renderInvestTab = renderInvestTab;
  C.renderReport = renderReport;
  C.renderGameList = renderGameList;
  C.renderSummary = renderSummary;
  C.renderSummaryV2 = renderSummaryV2;
})();
