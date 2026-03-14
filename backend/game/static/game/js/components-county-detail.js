/**
 * 县详情通用双标签卡片 — Game.countyDetail 命名空间
 * 供「邻县详情」（知县游戏）和「下辖县详情」（知府游戏）共用。
 *
 * 依赖：api.js, state.js, components-core.js（需先加载）
 */
(function () {
  "use strict";

  window.Game = window.Game || {};

  // ── 常量 ──────────────────────────────────────────────
  var ARCHETYPE_LABELS = { VIRTUOUS: "循吏", MIDDLING: "中庸", CORRUPT: "贪酷" };
  var ARCHETYPE_COLORS = { VIRTUOUS: "#1a7a4a", MIDDLING: "#6b5d45", CORRUPT: "#a93226" };
  var STYLE_LABELS = {
    minben: "民本型", zhengji: "政绩型", baoshou: "保守型",
    jinqu: "进取型", yuanhua: "圆滑型",
  };
  var STYLE_COLORS = {
    minben: "#27ae60", zhengji: "#2e86c1", baoshou: "#8a7a5a",
    jinqu: "#c0392b", yuanhua: "#8e44ad",
  };
  var TIER_COLORS = {
    "极差": "#c62828", "差": "#e53935", "稍差": "#ff7043", "勉强": "#fb8c00",
    "及格": "#f9a825", "稍好": "#7cb342", "良好": "#2e7d32", "优秀": "#1565c0",
  };
  var INDICATORS = ["民心", "治安", "商业", "文教", "人口规模", "县库状况"];
  var ADMIN_COST_LABELS = {
    official_salary: "官职俸禄", deputy_salary: "县丞俸禄", advisor_fee: "师爷费用",
    clerks_cost: "书吏成本", bailiff_cost: "衙役成本", school_cost: "县学成本",
    office_cost: "衙署开支", irrigation_maint: "水利维护", medical_maint: "医疗维护",
  };
  var GAZETTE_CAT_COLORS = {
    AI_DECISION: "#8e44ad", SETTLEMENT: "#27ae60", DISASTER: "#c0392b",
    INVESTMENT: "#2e86c1", TAX: "#d4a017", NEGOTIATION: "#16a085",
    PROMISE: "#8b4513", SYSTEM: "#8a7a5a",
  };
  var GAZETTE_CAT_LABELS = {
    AI_DECISION: "AI决策", SETTLEMENT: "月结算", DISASTER: "灾害",
    INVESTMENT: "投资", TAX: "税务", NEGOTIATION: "谈判", PROMISE: "承诺", SYSTEM: "系统",
  };
  var MONTH_NAMES = ["正月","二月","三月","四月","五月","六月","七月","八月","九月","十月","冬月","腊月"];

  // ── 工具函数 ──────────────────────────────────────────
  function el(id) { return document.getElementById(id); }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = (s !== null && s !== undefined) ? String(s) : "";
    return d.innerHTML;
  }

  function seasonName(n) {
    if (!n) return "";
    var year = Math.ceil(n / 12);
    var idx = (n - 1) % 12;
    return "第" + year + "年·" + MONTH_NAMES[idx];
  }

  function tierBadge(tier) {
    if (!tier || tier === "—") return '<span class="hint">—</span>';
    var color = TIER_COLORS[tier] || "#888";
    return '<span class="cdc-tier-badge" style="background:' + color + ';">' + esc(tier) + '</span>';
  }

  function trendArrow(t) {
    if (t === "↑") return '<span style="color:#2e7d32;font-weight:bold;">↑</span>';
    if (t === "↓") return '<span style="color:#c62828;font-weight:bold;">↓</span>';
    return '<span style="color:#aaa;">→</span>';
  }

  // 数值 → 档位（8档）
  function scoreToTier(v) {
    v = Math.round(v || 0);
    if (v >= 88) return "优秀";
    if (v >= 75) return "良好";
    if (v >= 63) return "稍好";
    if (v >= 50) return "及格";
    if (v >= 38) return "勉强";
    if (v >= 25) return "稍差";
    if (v >= 13) return "差";
    return "极差";
  }

  // ── 数据归一化 ─────────────────────────────────────────
  /**
   * 将两种不同来源的县域数据归一化为统一结构。
   * @param {object} rawData  原始 API 数据
   * @param {string} source   "neighbor" | "prefecture"
   */
  function normalizeCountyData(rawData, source) {
    if (source === "neighbor") {
      var cd = rawData.county_data || {};
      var totalPop = 0;
      (cd.villages || []).forEach(function (v) { totalPop += (v.population || 0); });
      return {
        source: "neighbor",
        ref_id: rawData.id,
        county_name: rawData.county_name || "",
        governor: {
          name: rawData.governor_name || "",
          archetype: rawData.governor_archetype || "MIDDLING",
          archetype_display: rawData.governor_archetype_display || "",
          style: rawData.governor_style || "",
          style_display: rawData.governor_style_display || "",
          bio: rawData.governor_bio || "",
        },
        indicators_raw: {
          morale:     Math.round(cd.morale || 0),
          security:   Math.round(cd.security || 0),
          commercial: Math.round(cd.commercial || 0),
          education:  Math.round(cd.education || 0),
          population: totalPop,
          treasury:   Math.round(cd.treasury || 0),
        },
        indicators_tier: null,
        indicators_trend: null,
        latest_report: null,
        infra: {
          irrigation_level:    cd.irrigation_level || 0,
          medical_level:       cd.medical_level || 0,
          school_level:        cd.school_level || 0,
          bailiff_level:       cd.bailiff_level || 0,
          tax_rate:            cd.tax_rate || 0.12,
          commercial_tax_rate: cd.commercial_tax_rate !== undefined ? cd.commercial_tax_rate : 0.03,
          has_granary:         !!cd.has_granary,
          active_investments:  cd.active_investments || [],
        },
        admin_cost:        cd.admin_cost,
        admin_cost_detail: cd.admin_cost_detail || null,
        villages:          cd.villages || [],
        markets:           cd.markets || [],
        analysis:          rawData.last_reasoning || "",
        reports:           null,
        quota:             null,
        annual_review:     null,
      };
    }

    if (source === "prefecture") {
      var gp = rawData.governor || {};
      var infra = rawData.infrastructure || {};
      var reports = rawData.reports || [];
      var latest = reports.length ? reports[reports.length - 1] : null;
      return {
        source: "prefecture",
        ref_id: rawData.unit_id,
        county_name: rawData.county_name || "",
        governor: {
          name: gp.name || "",
          archetype: gp.archetype || "MIDDLING",
          archetype_display: "",
          style: gp.style || "",
          style_display: "",
          bio: gp.bio || "",
        },
        indicators_raw: null,
        indicators_tier:  latest ? (latest.indicators || null) : null,
        indicators_trend: latest ? (latest.trend || null) : null,
        latest_report: latest,
        infra: {
          irrigation_level:    infra.irrigation_level || 0,
          medical_level:       infra.medical_level || 0,
          school_level:        infra.school_level || 0,
          bailiff_level:       infra.bailiff_level || 0,
          tax_rate:            infra.tax_rate || 0.12,
          commercial_tax_rate: infra.commercial_tax_rate !== undefined ? infra.commercial_tax_rate : 0.03,
          has_granary:         !!infra.has_granary,
          active_investments:  infra.active_investments || [],
        },
        admin_cost:        null,
        admin_cost_detail: null,
        villages:          [],
        markets:           [],
        analysis:          latest ? (latest.notes || latest.actions || "") : "",
        reports:           reports,
        quota:             rawData.quota || null,
        annual_review:     rawData.annual_review || null,
      };
    }

    return null;
  }

  // ── 区块渲染函数 ───────────────────────────────────────

  function renderGovernorSection(gov) {
    var arch = gov.archetype;
    var archLabel = gov.archetype_display || ARCHETYPE_LABELS[arch] || arch || "";
    var archColor = ARCHETYPE_COLORS[arch] || "#6b5d45";
    var styleLabel = gov.style_display || STYLE_LABELS[gov.style] || gov.style || "";
    var styleColor = STYLE_COLORS[gov.style] || "#8a7a5a";
    return (
      '<div class="cdc-section cdc-governor-section">' +
        '<div class="cdc-governor-row">' +
          (gov.name ? '<span class="cdc-gov-name">' + esc(gov.name) + '&nbsp;知县</span>' : '') +
          '<span class="cdc-arch-badge" style="background:' + archColor + ';">' + esc(archLabel) + '</span>' +
          (styleLabel ? '<span class="cdc-style-tag" style="background:' + styleColor + ';">' + esc(styleLabel) + '</span>' : '') +
        '</div>' +
        (gov.bio ? '<div class="cdc-gov-bio">' + esc(gov.bio) + '</div>' : '') +
      '</div>'
    );
  }

  function renderRawIndicatorsSection(raw) {
    var fields = [
      { key: "morale",     label: "民心" },
      { key: "security",   label: "治安" },
      { key: "commercial", label: "商业" },
      { key: "education",  label: "文教" },
      { key: "population", label: "人口" },
      { key: "treasury",   label: "县库" },
    ];
    var html = '<div class="cdc-section"><div class="cdc-section-title">县情指标</div><div class="cdc-stats-grid">';
    fields.forEach(function (f) {
      var val = raw[f.key];
      var badge = (f.key !== "population" && f.key !== "treasury") ? tierBadge(scoreToTier(val)) : "";
      var display = (f.key === "treasury") ? val + "两" : String(val);
      html += (
        '<div class="cdc-stat">' +
          '<span class="cdc-stat-label">' + f.label + '</span>' +
          '<span class="cdc-stat-value">' + display + '&nbsp;' + badge + '</span>' +
        '</div>'
      );
    });
    html += '</div></div>';
    return html;
  }

  function renderTierIndicatorsSection(tiers, trends, reportMonth) {
    var header = '<div class="cdc-section-title">县情指标' +
      (reportMonth ? '<span class="cdc-report-month">（' + seasonName(reportMonth) + '汇报）</span>' : '') +
      '</div>';
    if (!tiers) {
      return '<div class="cdc-section">' + header + '<p class="hint">尚无汇报数据</p></div>';
    }
    var html = '<div class="cdc-section">' + header + '<div class="cdc-stats-grid">';
    INDICATORS.forEach(function (k) {
      var val = tiers[k];
      var tr = trends ? (trends[k] || "→") : "→";
      html += (
        '<div class="cdc-stat">' +
          '<span class="cdc-stat-label">' + k + '</span>' +
          '<span class="cdc-stat-value">' +
            (val ? tierBadge(val) + '&nbsp;' + trendArrow(tr) : '<span class="hint">—</span>') +
          '</span>' +
        '</div>'
      );
    });
    html += '</div>';
    // 本期行动（来自 latest_report）
    return html + '</div>';
  }

  function renderInfraSection(infra) {
    var granaryHtml = infra.has_granary
      ? '<span class="cdc-infra-val cdc-granary-yes">有</span>'
      : '<span class="cdc-infra-val cdc-granary-no">无</span>';
    var html = (
      '<div class="cdc-section">' +
        '<div class="cdc-section-title">基础建设</div>' +
        '<div class="cdc-infra-grid">' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">水利</span><span class="cdc-infra-val">' + infra.irrigation_level + '/2级</span></div>' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">医疗</span><span class="cdc-infra-val">' + infra.medical_level + '/2级</span></div>' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">县学</span><span class="cdc-infra-val">' + infra.school_level + '/2级</span></div>' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">衙役</span><span class="cdc-infra-val">' + infra.bailiff_level + '/3级</span></div>' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">农税</span><span class="cdc-infra-val">' + (infra.tax_rate * 100).toFixed(1).replace(/\.0$/, "") + '%</span></div>' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">商税</span><span class="cdc-infra-val">' + (infra.commercial_tax_rate * 100).toFixed(1).replace(/\.0$/, "") + '%</span></div>' +
          '<div class="cdc-infra-item"><span class="cdc-infra-label">义仓</span>' + granaryHtml + '</div>' +
        '</div>'
    );
    var invs = infra.active_investments || [];
    if (invs.length) {
      html += '<div class="cdc-investments"><span class="cdc-investments-label">在建：</span>';
      html += invs.map(function (inv) {
        return esc(inv.description) + (inv.completion_season ? '（预计第' + inv.completion_season + '月）' : '');
      }).join('，');
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderVillagesSection(villages) {
    var html = (
      '<div class="cdc-section">' +
        '<div class="cdc-section-title">村庄</div>' +
        '<table class="cdc-data-table">' +
          '<thead><tr><th>村名</th><th>人口</th><th>耕地</th><th>地主占比</th><th>民心</th><th>村塾</th></tr></thead>' +
          '<tbody>'
    );
    villages.forEach(function (v) {
      html += (
        '<tr>' +
          '<td>' + esc(v.name) + '</td>' +
          '<td>' + v.population + '</td>' +
          '<td>' + v.farmland + '</td>' +
          '<td>' + (v.gentry_land_pct * 100).toFixed(0) + '%</td>' +
          '<td>' + Math.round(v.morale || 0) + '</td>' +
          '<td>' + (v.has_school ? '有' : '无') + '</td>' +
        '</tr>'
      );
    });
    html += '</tbody></table></div>';
    return html;
  }

  function renderMarketsSection(markets) {
    var html = '<div class="cdc-section"><div class="cdc-section-title">集市</div>';
    markets.forEach(function (m) {
      html += '<div class="cdc-market-row">' +
        esc(m.name) + ' — 商户 ' + m.merchants + '，月贸易额 ' + (m.gmv || 0) + ' 两' +
      '</div>';
    });
    html += '</div>';
    return html;
  }

  function renderAdminCostSection(adminCost, detail) {
    var keys = [
      "official_salary", "deputy_salary", "advisor_fee", "clerks_cost",
      "bailiff_cost", "school_cost", "office_cost", "irrigation_maint", "medical_maint",
    ];
    var rows = "";
    if (detail) {
      keys.forEach(function (k) {
        if (detail[k] === undefined) return;
        rows += '<div class="cdc-admin-row">' +
          '<span>' + esc(ADMIN_COST_LABELS[k] || k) + '</span>' +
          '<span>' + detail[k] + ' 两</span>' +
        '</div>';
      });
    }
    return (
      '<div class="cdc-section">' +
        '<div class="cdc-section-title">年度行政开支</div>' +
        '<details class="cdc-admin-details">' +
          '<summary>合计 ' + Math.round(adminCost || 0) + ' 两/年（展开明细）</summary>' +
          '<div class="cdc-admin-list">' +
            rows +
            '<div class="cdc-admin-row cdc-admin-total">' +
              '<span><strong>合计</strong></span>' +
              '<span><strong>' + Math.round(adminCost || 0) + ' 两</strong></span>' +
            '</div>' +
          '</div>' +
        '</details>' +
      '</div>'
    );
  }

  function renderAnalysisSection(text) {
    return (
      '<div class="cdc-section">' +
        '<div class="cdc-section-title">知县近期分析</div>' +
        '<div class="cdc-analysis-text">' + esc(text) + '</div>' +
      '</div>'
    );
  }

  function renderReportHistorySection(reports) {
    var html = (
      '<div class="cdc-section">' +
        '<div class="cdc-section-title">历史汇报趋势</div>' +
        '<div class="cdc-report-table-wrap">' +
          '<table class="cdc-data-table cdc-report-table">' +
            '<thead><tr><th>月份</th>'
    );
    INDICATORS.forEach(function (k) { html += '<th>' + k + '</th>'; });
    html += '</tr></thead><tbody>';
    for (var i = reports.length - 1; i >= 0; i--) {
      var r = reports[i];
      html += '<tr><td class="cdc-report-month-cell">' + seasonName(r.month) + '</td>';
      INDICATORS.forEach(function (k) {
        var v = (r.indicators && r.indicators[k]) || null;
        var tr = (r.trend && r.trend[k]) || "→";
        html += '<td>' + (v ? tierBadge(v) + trendArrow(tr) : '<span class="hint">—</span>') + '</td>';
      });
      html += '</tr>';
    }
    html += '</tbody></table></div></div>';
    return html;
  }

  // ── 总览 Tab 内容构建 ─────────────────────────────────
  function buildOverviewHTML(normalized, actions) {
    var html = "";

    html += renderGovernorSection(normalized.governor);

    // 县情指标：邻县用原始数值，府县用档位
    if (normalized.indicators_raw) {
      html += renderRawIndicatorsSection(normalized.indicators_raw);
    } else if (normalized.indicators_tier) {
      var reportMonth = normalized.latest_report ? normalized.latest_report.month : null;
      html += renderTierIndicatorsSection(
        normalized.indicators_tier,
        normalized.indicators_trend,
        reportMonth
      );
    } else {
      html += '<div class="cdc-section"><p class="hint">尚无县情数据</p></div>';
    }

    html += renderInfraSection(normalized.infra);

    if (normalized.villages.length) html += renderVillagesSection(normalized.villages);
    if (normalized.markets.length) html += renderMarketsSection(normalized.markets);

    if (normalized.admin_cost !== null && normalized.admin_cost !== undefined) {
      html += renderAdminCostSection(normalized.admin_cost, normalized.admin_cost_detail);
    }

    if (normalized.analysis) html += renderAnalysisSection(normalized.analysis);

    // 历史汇报趋势（仅府游戏，需至少2条）
    if (normalized.reports && normalized.reports.length > 1) {
      html += renderReportHistorySection(normalized.reports);
    }

    // 年度配额（仅府游戏）
    if (normalized.quota) {
      html += (
        '<div class="cdc-section">' +
          '<div class="cdc-section-title">年度配额</div>' +
          '<p class="cdc-quota-text">配额：<strong>' + normalized.quota + '</strong> 两</p>' +
        '</div>'
      );
    }

    // 操作按钮（府游戏传入）
    if (actions && actions.length) {
      html += '<div class="cdc-actions">';
      actions.forEach(function (a) {
        html += (
          '<button class="btn btn-small ' + (a.cls || "") + '"' +
            ' data-unit-id="' + normalized.ref_id + '"' +
            (a.countyName ? ' data-county-name="' + esc(a.countyName) + '"' : '') +
          '>' + a.label + '</button>'
        );
      });
      html += '</div>';
      // 核查结果区（components-prefecture.js 的 showInspectResult 继续使用此 id）
      html += '<div id="pref-inspect-result" class="pref-inspect-result hidden"></div>';
    }

    return html;
  }

  // ── 县志 Tab：懒加载 ──────────────────────────────────
  function loadGazette(gameId, normalized) {
    var body = el("cdc-gazette-body");
    if (!body) return;

    if (normalized.source === "neighbor") {
      body.innerHTML = '<p class="hint">加载中...</p>';
      Game.api.getNeighborEvents(gameId, normalized.ref_id, 150)
        .then(function (events) {
          if (!events || !events.length) {
            body.innerHTML = '<p class="hint">暂无事件记录</p>';
            return;
          }
          var html = '<div class="cdc-gazette-list">';
          events.forEach(function (evt) {
            var catColor = GAZETTE_CAT_COLORS[evt.category] || "#8a7a5a";
            var catLabel = GAZETTE_CAT_LABELS[evt.category] || evt.category;
            var sn = Game.seasonName ? Game.seasonName(evt.season) : seasonName(evt.season);
            html += (
              '<div class="event-log-item">' +
                '<div class="event-log-header">' +
                  '<span class="event-log-category" style="background:' + catColor + ';">' + catLabel + '</span>' +
                  '<span class="event-log-season">' + sn + '</span>' +
                '</div>' +
                '<div class="event-log-desc">' + esc(evt.description || evt.event_type) + '</div>' +
              '</div>'
            );
          });
          html += '</div>';
          body.innerHTML = html;
        })
        .catch(function () {
          body.innerHTML = '<p class="hint">加载失败，请重试</p>';
        });

    } else {
      // 府游戏：将历史汇报渲染为时间轴条目
      var reports = normalized.reports || [];
      if (!reports.length) {
        body.innerHTML = '<p class="hint">尚无汇报记录</p>';
        return;
      }
      var html = '<div class="cdc-gazette-list">';
      for (var i = reports.length - 1; i >= 0; i--) {
        var r = reports[i];
        var sn = seasonName(r.month);
        html += '<div class="cdc-gazette-report-entry">';
        html += (
          '<div class="event-log-header">' +
            '<span class="event-log-category" style="background:#27ae60;">月度汇报</span>' +
            '<span class="event-log-season">' + sn + '</span>' +
          '</div>'
        );
        // 六项指标行
        if (r.indicators) {
          html += '<div class="cdc-gazette-inds">';
          INDICATORS.forEach(function (k) {
            var v = r.indicators[k];
            var tr = r.trend ? (r.trend[k] || "→") : "→";
            if (v) {
              html += '<span class="cdc-gazette-ind">' + k + '&nbsp;' + tierBadge(v) + trendArrow(tr) + '</span>';
            }
          });
          html += '</div>';
        }
        if (r.actions) html += '<div class="cdc-gazette-report-text">本期行动：' + esc(r.actions) + '</div>';
        if (r.notes)   html += '<div class="cdc-gazette-report-text cdc-gazette-notes">特记：' + esc(r.notes) + '</div>';
        html += '</div>';
      }
      html += '</div>';
      body.innerHTML = html;
    }
  }

  // ── 打开弹窗（统一入口）──────────────────────────────
  /**
   * @param {object} rawData  API 返回的原始数据
   * @param {string} source   "neighbor" | "prefecture"
   * @param {Array}  actions  操作按钮配置（可选），格式：
   *   [{label, cls, countyName?}, ...]
   */
  function openCountyDetail(rawData, source, actions) {
    var modal = el("county-detail-modal");
    if (!modal) return;

    var normalized = normalizeCountyData(rawData, source);
    if (!normalized) return;

    // 标题
    var titleEl = el("cdc-title");
    if (titleEl) {
      titleEl.textContent = normalized.county_name +
        (normalized.governor.name ? " · " + normalized.governor.name : "");
    }

    // 重置为总览 Tab
    var tabBtns = modal.querySelectorAll(".cdc-tab-btn");
    var tabPanels = modal.querySelectorAll(".cdc-tab-content");
    tabBtns.forEach(function (b) {
      b.classList.toggle("active", b.dataset.cdcTab === "cdc-tab-overview");
    });
    tabPanels.forEach(function (p) {
      p.classList.toggle("hidden", p.id !== "cdc-tab-overview");
    });

    // 渲染总览
    var overviewBody = el("cdc-overview-body");
    if (overviewBody) overviewBody.innerHTML = buildOverviewHTML(normalized, actions);

    // 县志：清空，等待懒加载
    var gazetteBody = el("cdc-gazette-body");
    if (gazetteBody) {
      gazetteBody.dataset.loaded = "";
      gazetteBody.innerHTML = '<p class="hint">点击「县志」标签查看完整记录</p>';
    }

    // 缓存状态供懒加载使用
    var gameId = Game.state && Game.state.currentGame
      ? Game.state.currentGame.id
      : (Game.state && Game.state.prefectureGame ? Game.state.prefectureGame.game_id : null);
    modal._cdcNormalized = normalized;
    modal._cdcGameId = gameId;

    modal.classList.remove("hidden");
  }

  // ── Tab 切换（事件委托）──────────────────────────────
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".cdc-tab-btn");
    if (!btn) return;
    var targetId = btn.dataset.cdcTab;
    if (!targetId) return;
    var modal = btn.closest("#county-detail-modal");
    if (!modal) return;

    // 按钮高亮
    modal.querySelectorAll(".cdc-tab-btn").forEach(function (b) { b.classList.remove("active"); });
    btn.classList.add("active");

    // 面板切换
    modal.querySelectorAll(".cdc-tab-content").forEach(function (p) { p.classList.add("hidden"); });
    var panel = el(targetId);
    if (panel) panel.classList.remove("hidden");

    // 县志懒加载
    if (targetId === "cdc-tab-gazette") {
      var gazetteBody = el("cdc-gazette-body");
      if (gazetteBody && gazetteBody.dataset.loaded !== "1") {
        gazetteBody.dataset.loaded = "1";
        loadGazette(modal._cdcGameId, modal._cdcNormalized);
      }
    }
  });

  // ── 暴露 ──────────────────────────────────────────────
  window.Game.countyDetail = {
    open: openCountyDetail,
  };

})();
