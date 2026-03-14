/**
 * Prefecture (知府) UI components — Game.prefecture namespace
 */
(function () {
  "use strict";

  window.Game = window.Game || {};

  // ── Tier config ──
  var TIER_COLORS = {
    "极差": "#c62828",
    "差":   "#e53935",
    "稍差": "#ff7043",
    "勉强": "#fb8c00",
    "及格": "#f9a825",
    "稍好": "#7cb342",
    "良好": "#2e7d32",
    "优秀": "#1565c0",
  };

  var ARCHETYPE_LABELS = {
    "VIRTUOUS": "循吏",
    "MIDDLING":  "中庸",
    "CORRUPT":   "贪酷",
  };
  var ARCHETYPE_COLORS = {
    "VIRTUOUS": "#1a7a4a",
    "MIDDLING":  "#6b5d45",
    "CORRUPT":   "#a93226",
  };

  var STYLE_LABELS = {
    "minben":   "民本型",
    "zhengji":  "政绩型",
    "baoshou":  "保守型",
    "jinqu":    "进取型",
    "yuanhua":  "圆滑型",
  };

  var MONTH_NAMES = [
    "正月","二月","三月","四月","五月","六月",
    "七月","八月","九月","十月","冬月","腊月",
  ];
  var REPORT_MONTHS = [2, 5, 8, 11];
  var KEY_MONTH_LABELS = { 1: "定额", 10: "府试", 12: "评议" };
  var TODO_TYPE_META = {
    year_end_review: { icon: "📜", action: "" },
    judicial_case:   { icon: "⚖", action: "前往司法" },
    county_disaster: { icon: "🌧", action: "查看州县" },
    exam_result:     { icon: "📝", action: "查看府试" },
  };

  // ── helpers ──
  function el(id) { return document.getElementById(id); }
  function h(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  }

  function tierBadge(tier, small) {
    if (!tier) return "";
    var color = TIER_COLORS[tier] || "#888";
    var sz = small ? "font-size:11px;padding:1px 5px;" : "font-size:12px;padding:2px 7px;";
    return '<span class="pref-tier-badge" style="background:' + color + ';' + sz + '">' + esc(tier) + '</span>';
  }

  function archetypeBadge(arch) {
    var label = ARCHETYPE_LABELS[arch] || arch;
    var color = ARCHETYPE_COLORS[arch] || "#888";
    return '<span class="pref-arch-badge" style="background:' + color + ';">' + label + '</span>';
  }

  function trendArrow(t) {
    if (t === "↑") return '<span style="color:#2e7d32;">↑</span>';
    if (t === "↓") return '<span style="color:#c62828;">↓</span>';
    return '<span style="color:#888;">→</span>';
  }

  function seasonName(n) {
    if (n > 36) return "任期结束";
    var year = Math.ceil(n / 12);
    var idx = (n - 1) % 12;
    return "第" + year + "年·" + MONTH_NAMES[idx];
  }

  function moy(season) { return (season - 1) % 12 + 1; }

  // ── Header ──
  function renderPrefectureHeader(data) {
    var hdr = el("pref-header-info");
    if (!hdr || !data) return;
    hdr.innerHTML =
      '<span class="pref-role-badge">知府</span>' +
      '<strong id="pref-name-display">' + esc(data.prefecture_name) + '</strong>' +
      '<span id="pref-season-display">' + seasonName(data.current_season) + '</span>' +
      '<span id="pref-treasury-display">府库：' + data.treasury + '两</span>';
  }

  // ── Calendar strip ──
  function renderCalendarStrip(current_season) {
    var container = el("pref-calendar-strip");
    if (!container) return;
    var curMoy = moy(current_season);
    var html = '<div class="pref-calendar">';
    for (var m = 1; m <= 12; m++) {
      var cls = "pref-cal-month";
      if (m === curMoy) cls += " pref-cal-current";
      var isReport = REPORT_MONTHS.indexOf(m) !== -1;
      var keyLabel = KEY_MONTH_LABELS[m] || "";
      html += '<div class="' + cls + '">';
      html += '<div class="pref-cal-name">' + MONTH_NAMES[m - 1] + '</div>';
      if (isReport) html += '<div class="pref-cal-dot pref-cal-report" title="汇报月"></div>';
      if (keyLabel) html += '<div class="pref-cal-label">' + keyLabel + '</div>';
      html += '</div>';
    }
    html += '</div>';
    container.innerHTML = html;
  }

  // ── Overview Tab ──
  function renderPrefectureOverview(data) {
    if (!data) return;
    renderPrefectureHeader(data);
    renderCalendarStrip(data.current_season);

    // 正月显示分配定额按钮，其他月份隐藏
    var quotaBtn = el("btn-pref-quota");
    if (quotaBtn) {
      quotaBtn.classList.toggle("hidden", moy(data.current_season) !== 1);
    }

    // Quota progress
    var quotaEl = el("pref-quota-progress");
    if (quotaEl) {
      var pct = data.annual_quota > 0
        ? Math.min(100, Math.round((data.treasury_collected || 0) / data.annual_quota * 100))
        : 0;
      quotaEl.innerHTML =
        '<div class="pref-section-title">省级定额完成情况</div>' +
        '<div class="pref-quota-bar-wrap">' +
          '<div class="pref-quota-bar" style="width:' + pct + '%"></div>' +
        '</div>' +
        '<div class="pref-quota-text">已征收 <strong>' + (data.treasury_collected || 0) +
        '</strong> / 定额 <strong>' + data.annual_quota + '</strong> 两</div>';
    }

    // Key metrics
    var metricsEl = el("pref-key-metrics");
    if (metricsEl) {
      var counties = data.counties || [];
      metricsEl.innerHTML =
        '<div class="pref-section-title">府域概况</div>' +
        '<div class="pref-metrics-grid">' +
          '<div class="pref-metric-item"><span class="pref-metric-label">府库余银</span>' +
            '<span class="pref-metric-val">' + data.treasury + '两</span></div>' +
          '<div class="pref-metric-item"><span class="pref-metric-label">下辖县/州</span>' +
            '<span class="pref-metric-val">' + counties.length + '个</span></div>' +
          '<div class="pref-metric-item"><span class="pref-metric-label">府学</span>' +
            '<span class="pref-metric-val">' + (data.school_level || 0) + '级</span></div>' +
          '<div class="pref-metric-item"><span class="pref-metric-label">交通基建</span>' +
            '<span class="pref-metric-val">' + (data.road_level || 0) + '级</span></div>' +
          '<div class="pref-metric-item"><span class="pref-metric-label">水利基建</span>' +
            '<span class="pref-metric-val">' + (data.river_work_level || 0) + '级</span></div>' +
        '</div>';
    }

    renderTodoList(data.todo_items || []);
  }

  function renderTodoList(todoItems) {
    var container = el("pref-todo-list");
    if (!container) return;
    var html = '<div class="pref-section-title">待办事项提醒</div>';
    if (!todoItems || !todoItems.length) {
      container.innerHTML = html + '<div class="pref-empty-state">当前暂无待办事项。</div>';
      return;
    }
    html += '<div class="pref-todo-list">';
    todoItems.forEach(function (item) {
      var meta = TODO_TYPE_META[item.type] || { icon: "•", action: "" };
      var severity = item.severity || "low";
      var detail = "";
      if (item.summary) {
        detail = item.summary;
      } else if (item.county_names && item.county_names.length) {
        detail = item.county_names.join("、");
      } else if (item.count && item.type === "judicial_case") {
        detail = "请尽快审阅并作出裁断";
      } else if (item.type === "year_end_review") {
        detail = "请在本月内完成对各下属的年度评议";
      }

      html += '<div class="pref-todo-item severity-' + severity + '">' +
        '<div class="pref-todo-main">' +
          '<div class="pref-todo-title"><span class="pref-todo-icon">' + meta.icon + '</span>' +
            esc(item.title || "待办事项") + '</div>';
      if (detail) {
        html += '<div class="pref-todo-detail">' + esc(detail) + '</div>';
      }
      html += '</div>';
      if (meta.action && item.target_tab && item.target_tab !== "pref-tab-overview") {
        html += '<button class="btn btn-small pref-todo-action" data-target-tab="' + esc(item.target_tab) + '">' +
          meta.action + '</button>';
      } else {
        html += '<span class="pref-todo-status">待处理</span>';
      }
      html += '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
  }

  // ── Counties Tab (full table) ──
  function renderPrefectureCounties(counties) {
    var container = el("pref-counties-table-wrap");
    if (!container) return;
    if (!counties || !counties.length) {
      container.innerHTML = '<p class="hint">暂无下辖县</p>';
      return;
    }

    var indicators = ["民心", "治安", "商业", "文教", "人口规模", "县库状况"];
    var html = '<table class="pref-counties-table">' +
      '<thead><tr>' +
        '<th>县/州</th><th>知县</th><th>类型</th>';
    indicators.forEach(function (k) { html += '<th>' + k + '</th>'; });
    html += '<th>年度配额</th><th>操作</th></tr></thead><tbody>';

    counties.forEach(function (c) {
      var latest = c.latest_report;
      var ind = (latest && latest.indicators) || {};
      var trend = (latest && latest.trend) || {};
      html += '<tr class="pref-county-row" data-unit-id="' + c.unit_id + '">' +
        '<td><strong>' + esc(c.county_name) + '</strong></td>' +
        '<td>' + esc(c.governor_name) + '</td>' +
        '<td>' + archetypeBadge(c.governor_archetype) + '</td>';
      indicators.forEach(function (k) {
        var val = ind[k];
        html += '<td>' + (val ? tierBadge(val) + trendArrow(trend[k] || "→") : '<span class="hint">—</span>') + '</td>';
      });
      html += '<td>' + (c.quota || 0) + '两</td>';
      html += '<td><button class="btn btn-small btn-county-detail" data-unit-id="' + c.unit_id + '">详情</button></td>';
      html += '</tr>';
    });
    html += '</tbody></table>';

    if (counties.length && counties[0].latest_report) {
      html += '<p class="hint pref-report-time">数据来自 ' +
        seasonName(counties[0].latest_report.month) + ' 汇报</p>';
    }
    container.innerHTML = html;
  }

  function renderPersonnelTab(data) {
    var container = el("pref-personnel-content");
    if (!container) return;

    if (!data || !data.available) {
      container.innerHTML = '<p class="hint">当前不在年度评议窗口。</p>';
      return;
    }

    var summary = data.summary || {};
    var counties = data.counties || [];
    var html = '<div class="pref-section-title">年度评议</div>';
    html += '<div class="pref-personnel-summary">';
    html += '<span>共 ' + (summary.total || 0) + ' 名下属</span>';
    html += '<span>已自陈 ' + (summary.submitted || 0) + '</span>';
    html += '<span>已初评 ' + (summary.reviewed || 0) + '</span>';
    if (data.phase === "published") {
      html += '<span>最终为差 ' + (summary.poor || 0) + '</span>';
    }
    html += '</div>';

    if (!counties.length) {
      container.innerHTML = html + '<p class="hint">暂无下辖县州。</p>';
      return;
    }

    counties.forEach(function (item) {
      var snapshot = item.objective_snapshot || {};
      var selfStatement = item.self_statement || {};
      var review = item.prefect_review || {};
      var recheck = item.governor_recheck || {};
      var replacement = item.replacement || {};
      html += '<div class="pref-personnel-card">';
      html += '<div class="pref-personnel-header">';
      html += '<div><strong>' + esc(item.county_name) + '</strong> · ' + esc(item.review_subject_name || item.governor_name || "") + '</div>';
      html += '<div>' + archetypeBadge(item.review_subject_archetype || item.governor_archetype) + '</div>';
      html += '</div>';

      html += '<div class="pref-personnel-metrics">';
      html += '<span>税赋完成 ' + (snapshot.quota_completion_pct || 0) + '%</span>';
      html += '<span>民心 ' + (snapshot.morale || 0) + '</span>';
      html += '<span>治安 ' + (snapshot.security || 0) + '</span>';
      html += '<span>商业 ' + (snapshot.commercial || 0) + '</span>';
      html += '<span>文教 ' + (snapshot.education || 0) + '</span>';
      html += '</div>';

      if (selfStatement.achievements) {
        html += '<div class="pref-personnel-block">';
        html += '<div class="pref-section-title">年度自陈</div>';
        html += '<div><strong>政绩完成情况：</strong>' + esc(selfStatement.achievements || "") + '</div>';
        html += '<div><strong>未完事项：</strong>' + esc(selfStatement.unfinished || "") + '</div>';
        html += '<div><strong>过失记录：</strong>' + esc(selfStatement.faults || "") + '</div>';
        html += '<div><strong>来年规划：</strong>' + esc(selfStatement.plan || "") + '</div>';
        html += '</div>';
      }

      if (data.phase === "review") {
        html += '<div class="pref-personnel-block">';
        html += '<div class="pref-section-title">知府评议</div>';
        html += '<div class="pref-review-form">';
        html += '<label>等级<select class="pref-review-grade" data-unit-id="' + item.unit_id + '">';
        ["优", "良", "中", "差"].forEach(function (grade) {
          var selected = review.grade === grade ? ' selected' : '';
          html += '<option value="' + grade + '"' + selected + '>' + grade + '</option>';
        });
        html += '</select></label>';
        html += '<label>做得好的地方<textarea class="pref-review-strengths" data-unit-id="' + item.unit_id + '" rows="2">' + esc(review.strengths || "") + '</textarea></label>';
        html += '<label>做得不好的地方<textarea class="pref-review-weaknesses" data-unit-id="' + item.unit_id + '" rows="2">' + esc(review.weaknesses || "") + '</textarea></label>';
        html += '<label>来年重点建议<textarea class="pref-review-focus" data-unit-id="' + item.unit_id + '" rows="2">' + esc(review.focus || "") + '</textarea></label>';
        html += '<div class="pref-review-actions"><button class="btn btn-primary btn-small btn-pref-personnel-save" data-unit-id="' + item.unit_id + '">保存评议</button></div>';
        html += '</div>';
        html += '</div>';
      } else if (review.grade) {
        html += '<div class="pref-personnel-block pref-personnel-review-result">';
        html += '<div class="pref-section-title">知府初评：<strong>' + esc(review.grade) + '</strong></div>';
        html += '<div><strong>做得好的地方：</strong>' + esc(review.strengths || "") + '</div>';
        html += '<div><strong>做得不好的地方：</strong>' + esc(review.weaknesses || "") + '</div>';
        html += '<div><strong>来年重点建议：</strong>' + esc(review.focus || "") + '</div>';
        html += '</div>';
      }

      if (recheck.final_grade) {
        html += '<div class="pref-personnel-block pref-personnel-governor-result">';
        html += '<div class="pref-section-title">巡抚复核：<strong>' + esc(recheck.final_grade) + '</strong></div>';
        html += '<div>' + esc(recheck.comment || "") + '</div>';
        if (replacement.incoming_name) {
          html += '<div class="pref-personnel-replacement">正月已换任：' + esc(replacement.incoming_name) + ' 到任。</div>';
        }
        html += '</div>';
      }

      html += '</div>';
    });

    container.innerHTML = html;
  }

  // ── County Detail Modal ──
  function openPrefectureCountyDetail(detail) {
    if (!detail) return;
    // 使用共享模块打开统一双标签详情卡片
    Game.countyDetail.open(detail, "prefecture", [
      { label: "通判核账", cls: "btn-inspect-tongpan" },
      { label: "推官巡查", cls: "btn-inspect-tuiguan" },
      { label: "下达指令", cls: "btn-directive", countyName: detail.county_name },
    ]);
  }

  function showInspectResult(data) {
    var el2 = el("pref-inspect-result");
    if (!el2) return;

    var labels = {
      treasury: "县库", last_remit: "本期上缴", tax_rate: "农业税率",
      commercial_tax_rate: "商税税率", security: "治安", morale: "民心",
      population: "人口", education: "文教",
    };

    function _renderOne(r) {
      var h = '<div class="pref-inspect-county-block">';
      h += '<div class="pref-inspect-header">【' + esc(r.type) + '】' + esc(r.county_name || '') + '</div>';
      h += '<div class="pref-inspect-grid">';
      Object.keys(labels).forEach(function (k) {
        if (r[k] !== undefined) {
          var v = k.indexOf("rate") !== -1 ? (r[k] * 100).toFixed(1) + "%" : r[k];
          h += '<div class="pref-inspect-item"><span>' + labels[k] + '</span><strong>' + v + '</strong></div>';
        }
      });
      h += '</div></div>';
      return h;
    }

    var html = "";
    // 新格式：{results:[...], road_level, bonus_counties}
    if (data.results) {
      if (data.bonus_counties > 0) {
        html += '<div class="pref-inspect-road-notice">交通基建加成：本次核查覆盖 ' + data.results.length + ' 县</div>';
      }
      data.results.forEach(function (r) { html += _renderOne(r); });
    } else {
      // 兼容旧格式（单县 dict）
      html = _renderOne(data);
    }

    el2.innerHTML = html;
    el2.classList.remove("hidden");
  }

  // ── Quota Modal ──
  function openQuotaModal(overview) {
    var modal = el("pref-quota-modal");
    if (!modal) return;
    var counties = overview.counties || [];
    var annualQuota = overview.annual_quota || 0;

    var html = '<div class="pref-quota-header">省级定额：<strong>' + annualQuota + '</strong> 两<br>' +
      '<small>系统建议按各县承载能力均摊，可调整</small></div>';
    html += '<table class="pref-quota-table"><thead><tr><th>县/州</th><th>知县</th><th>年度配额（两）</th></tr></thead><tbody>';
    var suggested = counties.length ? Math.round(annualQuota / counties.length) : 0;
    counties.forEach(function (c) {
      var current = c.quota || suggested;
      html += '<tr><td>' + esc(c.county_name) + '</td><td>' + esc(c.governor_name) + '</td>' +
        '<td><input class="pref-quota-input" type="number" min="0" step="100" ' +
          'data-unit-id="' + c.unit_id + '" value="' + current + '"></td></tr>';
    });
    html += '</tbody></table>';
    html += '<div class="pref-quota-total">已分配合计：<strong id="pref-quota-running-total">0</strong> 两 / ' + annualQuota + ' 两</div>';

    el("pref-quota-modal-body").innerHTML = html;
    modal.classList.remove("hidden");
    updateQuotaRunningTotal();
  }

  function updateQuotaRunningTotal() {
    var inputs = document.querySelectorAll(".pref-quota-input");
    var total = 0;
    for (var i = 0; i < inputs.length; i++) {
      total += parseFloat(inputs[i].value) || 0;
    }
    var span = el("pref-quota-running-total");
    if (span) span.textContent = total;
  }

  function collectQuotaAssignments() {
    var inputs = document.querySelectorAll(".pref-quota-input");
    var result = {};
    for (var i = 0; i < inputs.length; i++) {
      result[inputs[i].dataset.unitId] = parseFloat(inputs[i].value) || 0;
    }
    return result;
  }

  // ── Directive Modal ──
  function openDirectiveModal(unitId, countyName) {
    var modal = el("pref-directive-modal");
    if (!modal) return;
    el("pref-directive-modal-title").textContent = "向 " + countyName + " 下达指令";
    el("pref-directive-input").value = "";
    el("pref-directive-modal").dataset.unitId = unitId;
    modal.classList.remove("hidden");
    el("pref-directive-input").focus();
  }

  // ── Month Report ──
  function renderPrefectureReport(result) {
    var container = el("pref-advance-modal-body");
    if (!container) return;
    var html =
      '<div class="pref-report-header">' + seasonName(result.season) + ' 月度汇总</div>' +
      '<div class="pref-report-row"><span>各县上缴合计</span><strong>' + result.remit_total + ' 两</strong></div>' +
      '<div class="pref-report-row"><span>府库现余</span><strong>' + result.treasury + ' 两</strong></div>';

    if (result.report_generated) {
      html += '<div class="pref-report-note">📋 本月为汇报月，已更新各县状况档位</div>';
    }
    if (result.construction_completed && result.construction_completed.length) {
      result.construction_completed.forEach(function (msg) {
        html += '<div class="pref-report-note pref-note-build">🏗 ' + esc(msg) + '</div>';
      });
    }
    if (result.exam_result) {
      var er = result.exam_result;
      html += '<div class="pref-report-note pref-note-exam">📝 第' + er.year +
        '年府试已举行，录取 <strong>' + er.count + '</strong> 名。' +
        (er.top_10 && er.top_10.length ? '首名：' + esc(er.top_10[0].name) + '（' + esc(er.top_10[0].county) + '，能力' + er.top_10[0].ability + '）' : '') +
        ' <a href="#" class="pref-exam-detail-link">查看名单</a></div>';
    }
    if (result.year_end_review_pending) {
      html += '<div class="pref-report-note pref-note-review">📜 年度评议窗口已开放（腊月）</div>';
    }
    if (result.personnel_opened) {
      html += '<div class="pref-report-note pref-note-review">🗂 冬月人事窗口已开放，下属已递交年度自陈 ' + (result.personnel_ready_count || 0) + ' 份。</div>';
    }
    if (result.personnel_result) {
      html += '<div class="pref-report-note pref-note-review">🧾 巡抚复核已公布：完成 ' +
        (result.personnel_result.finalized || 0) + ' 份，改定 ' +
        (result.personnel_result.adjusted || 0) + ' 份，换任 ' +
        (result.personnel_result.replaced || 0) + ' 人。</div>';
    }
    container.innerHTML = html;
  }

  // ── Infra (基础建设) Tab ──
  var INFRA_PROJECT_ICONS = {
    school:  "🏫",
    road:    "🛤",
    granary: "🏚",
    river:   "🌊",
  };

  function renderInfraPanel(data, talentData) {
    var container = el("pref-infra-content");
    if (!container || !data) return;

    var html = '<div class="pref-section-title">府库余银：<strong>' + data.treasury + '</strong> 两</div>';

    // Investment cards grid
    html += '<div class="pref-infra-grid">';
    (data.projects || []).forEach(function (p) {
      var icon = INFRA_PROJECT_ICONS[p.project] || "🔨";
      var levelBar = '';
      for (var i = 1; i <= p.max_level; i++) {
        levelBar += '<span class="pref-infra-pip' + (i <= p.current_level ? ' filled' : '') + '"></span>';
      }
      html += '<div class="pref-infra-card' + (p.maxed ? ' maxed' : '') + '">';
      html += '<div class="pref-infra-card-header"><span class="pref-infra-icon">' + icon + '</span>' +
        '<span class="pref-infra-label">' + esc(p.label) + '</span>' +
        '<span class="pref-infra-level">' + p.current_level + '/' + p.max_level + '级</span></div>';
      html += '<div class="pref-infra-pips">' + levelBar + '</div>';

      if (p.maxed) {
        html += '<div class="pref-infra-status maxed-label">已建成</div>';
      } else if (p.in_queue) {
        var qItem = null;
        (data.construction_queue || []).forEach(function (q) {
          if (q.project === p.project) qItem = q;
        });
        var rem = qItem ? qItem.months_remaining : '?';
        html += '<div class="pref-infra-status building">建设中，剩余 <strong>' + rem + '</strong> 月</div>';
      } else {
        html += '<div class="pref-infra-cost">建造费：<strong>' + p.next_cost + '</strong> 两 / 工期 <strong>' + p.next_duration + '</strong> 月</div>';
        html += '<button class="btn btn-small pref-infra-btn' + (p.can_invest ? '' : ' disabled') +
          '" data-project="' + p.project + '" data-level="' + p.next_level + '"' +
          (p.can_invest ? '' : ' disabled') + '>兴建</button>';
      }

      // ── 府学：嵌入才池 & 府试信息 ──
      if (p.project === 'school' && talentData) {
        html += '<div class="pref-school-extra">';

        // 才池折叠
        var poolId = 'pref-school-pool';
        html += '<div class="pref-school-toggle" data-target="' + poolId + '">' +
          '才池 · <strong>' + talentData.total + '</strong> 人 &nbsp;|&nbsp; 累计门生 <strong>' + (talentData.total_disciples || 0) + '</strong> 人' +
          '<span class="pref-school-arrow">&#9654;</span>' +
          '</div>';
        html += '<div class="pref-school-body hidden" id="' + poolId + '">';
        if (talentData.by_county && talentData.by_county.length) {
          html += '<table class="pref-talent-table"><thead><tr><th>县/州</th><th>人数</th><th>平均能力</th></tr></thead><tbody>';
          talentData.by_county.forEach(function (row) {
            html += '<tr><td>' + esc(row.county_name) + '</td><td>' + row.count + '</td><td>' + row.avg_ability + '</td></tr>';
          });
          html += '</tbody></table>';
        } else {
          html += '<p class="hint" style="margin:6px 0;">才池尚无人才</p>';
        }
        html += '</div>';

        // 历届府试折叠（最新在前）
        var exams = talentData.exam_results || [];
        for (var ei = exams.length - 1; ei >= 0; ei--) {
          var er = exams[ei];
          var examId = 'pref-exam-' + er.year;
          html += '<div class="pref-school-toggle pref-school-toggle-exam" data-target="' + examId + '">' +
            '第' + er.year + '年府试 · 录取 <strong>' + er.count + '</strong> 人' +
            '<span class="pref-school-arrow">&#9654;</span>' +
            '</div>';
          html += '<div class="pref-school-body hidden" id="' + examId + '">';
          if (er.county_counts && Object.keys(er.county_counts).length) {
            html += '<div class="pref-exam-counties">';
            Object.keys(er.county_counts).forEach(function (cname) {
              html += '<span class="pref-exam-county-tag">' + esc(cname) + ' ' + er.county_counts[cname] + '人</span>';
            });
            html += '</div>';
          }
          if (er.top_10 && er.top_10.length) {
            html += '<table class="pref-exam-top-table"><thead><tr><th>名次</th><th>姓名</th><th>籍贯</th><th>能力</th></tr></thead><tbody>';
            er.top_10.forEach(function (t, idx) {
              html += '<tr><td>' + (idx + 1) + '</td><td>' + esc(t.name) + '</td>' +
                '<td>' + esc(t.county) + '·' + esc(t.village) + '</td><td>' + t.ability + '</td></tr>';
            });
            html += '</tbody></table>';
          }
          html += '</div>';
        }

        html += '</div>'; // pref-school-extra
      }

      html += '</div>'; // pref-infra-card
    });
    html += '</div>';

    container.innerHTML = html;
  }

  // ── Prefecture log entry (府志) ──
  function buildPrefLogEntry(result) {
    var title = seasonName(result.season) + " 府政月报";
    var bodyHtml = el("pref-advance-modal-body") ? el("pref-advance-modal-body").innerHTML : "";
    var entry = document.createElement("div");
    entry.className = "monthly-log-entry";
    entry.innerHTML =
      '<div class="monthly-log-toggle">' +
        '<span class="monthly-log-title">' + esc(title) + ' · 征收' + result.remit_total + '两</span>' +
        '<span class="monthly-log-arrow">&#9654;</span>' +
      '</div>' +
      '<div class="monthly-log-body">' + bodyHtml + '</div>';
    entry.querySelector(".monthly-log-toggle").addEventListener("click", function () {
      entry.classList.toggle("expanded");
    });
    return entry;
  }

  // ── Expose ──
  // ==================== 司法卷宗渲染 ====================

  var DIFF_LABEL = { '新手': '初级卷宗', '进阶': '进阶卷宗', '高难': '疑难重案' };
  var ACTION_DESC = {
    '核准原判': '签发复核文书，认可县级初审结果。',
    '驳回重审': '以证据不足为由发回县级重审，限期补充证据。',
    '提审改判': '调取案犯至府衙，由推官直接提审重新裁决。',
  };

  function renderJudicialCase(c) {
    var sm = c.suspicion_markers || {};
    var critical = (sm.critical || []);
    var secondary = (sm.secondary || []);
    var attachments = (c.attachments || []);

    var html = '<div class="judicial-case-wrap">';

    // 卷宗头
    html += '<div class="judicial-case-meta">';
    html += '<span class="judicial-meta-item">来源：' + (c.source_county || '—') + '</span>';
    html += '<span class="judicial-meta-item">类型：' + (c.category || '') + '</span>';
    html += '</div>';

    // 卷宗正文
    html += '<div class="judicial-dossier">';
    html += '<div class="judicial-dossier-label">卷宗正文</div>';
    html += '<p class="judicial-dossier-text">' + (c.dossier_text || '') + '</p>';
    html += '</div>';

    // 附件
    if (attachments.length) {
      html += '<div class="judicial-attachments"><span class="judicial-attach-label">附件：</span>';
      html += attachments.map(function (a) { return '<span class="judicial-attach-item">' + a + '</span>'; }).join('');
      html += '</div>';
    }

    // 推官标注疑点
    if (critical.length || secondary.length) {
      html += '<div class="judicial-suspicion">';
      html += '<div class="judicial-suspicion-label">推官标注疑点</div>';
      if (critical.length) {
        html += '<ul class="judicial-suspicion-list critical">';
        critical.forEach(function (s) { html += '<li>⚠ ' + s + '</li>'; });
        html += '</ul>';
      }
      if (secondary.length) {
        html += '<ul class="judicial-suspicion-list secondary">';
        secondary.forEach(function (s) { html += '<li>◇ ' + s + '</li>'; });
        html += '</ul>';
      }
      html += '</div>';
    }

    html += '</div>';
    return html;
  }

  function renderJudicialResult(result) {
    var effects = result.effects || {};
    var applied = result.applied_state || {};
    var chains  = result.chain_events || [];

    var parts = [];
    if (effects.prestige)         parts.push('民望 ' + (effects.prestige > 0 ? '+' : '') + effects.prestige);
    if (effects.magistrate_favor) parts.push('知县好感 ' + (effects.magistrate_favor > 0 ? '+' : '') + effects.magistrate_favor);
    if (effects.inspector_favor)  parts.push('按察使好感 ' + (effects.inspector_favor > 0 ? '+' : '') + effects.inspector_favor);
    if (effects.treasury)         parts.push('府库 ' + (effects.treasury > 0 ? '+' : '') + effects.treasury + ' 两');

    var html = '<div class="judicial-result-wrap">';
    html += '<div class="judicial-result-action">决断：' + result.action + '</div>';
    if (parts.length) {
      html += '<div class="judicial-result-effects">' + parts.join('　') + '</div>';
    }
    if (applied.judicial_prestige != null || applied.inspector_favor != null) {
      html += '<div class="judicial-result-effects">';
      if (applied.judicial_prestige != null) {
        html += '当前司法声望：' + applied.judicial_prestige;
      }
      if (applied.inspector_favor != null) {
        html += '　按察使观感：' + applied.inspector_favor;
      }
      html += '</div>';
    }
    if (chains.length) {
      html += '<div class="judicial-chain-label">后续影响：</div>';
      html += '<ul class="judicial-chain-list">';
      chains.forEach(function (e) { html += '<li>' + e + '</li>'; });
      html += '</ul>';
    }
    if (effects.treasury) {
      html += '<div class="judicial-result-treasury">府库余额：' + result.treasury + ' 两</div>';
    }
    html += '</div>';
    return html;
  }

  // ── Judicial Tab ──
  var DIFF_CLASS = { '新手': 'difficulty-easy', '进阶': 'difficulty-medium', '高难': 'difficulty-hard' };

  function renderJudicialTab(data) {
    var container = el("pref-judicial-content");
    if (!container) return;

    var pending = data.pending_cases || [];
    var log     = data.judicial_log  || [];
    var meta    = data.judicial_meta || {};

    var html = '<div class="pref-section-title">待审卷宗</div>';
    html += '<div class="pref-judicial-meta">司法声望：<strong>' + (meta.judicial_prestige != null ? meta.judicial_prestige : 50) +
      '</strong>　按察使观感：<strong>' + (meta.inspector_favor != null ? meta.inspector_favor : 50) + '</strong></div>';

    if (!pending.length) {
      html += '<p class="hint">暂无待审卷宗。季末（三、六、九、腊月）将自动生成新案件。</p>';
    } else {
      html += '<div class="judicial-pending-list">';
      pending.forEach(function (c, idx) {
        var diffCls = DIFF_CLASS[c.difficulty] || 'difficulty-easy';
        html +=
          '<div class="judicial-pending-card">' +
            '<div class="judicial-pending-header">' +
              '<span class="judicial-badge ' + diffCls + '">' + esc(c.difficulty) + '</span>' +
              '<span class="judicial-pending-title">【' + esc(c.category) + '】' + esc(c.case_name) + '</span>' +
              '<span class="judicial-pending-county">' + esc(c.source_county || '') + '</span>' +
            '</div>' +
            '<p class="judicial-pending-excerpt">' + esc((c.dossier_text || '').slice(0, 80)) + (c.dossier_text && c.dossier_text.length > 80 ? '…' : '') + '</p>' +
            '<button class="btn btn-small btn-open-judicial-case" data-idx="' + idx + '">查阅卷宗</button>' +
          '</div>';
      });
      html += '</div>';
    }

    // Judicial log
    if (log.length) {
      html += '<div class="pref-section-title" style="margin-top:20px;">司法档案</div>';
      html += '<table class="pref-judicial-log-table"><thead><tr>' +
        '<th>案名</th><th>类型</th><th>决断</th><th>民望</th><th>府库</th>' +
        '</tr></thead><tbody>';
      for (var i = log.length - 1; i >= 0; i--) {
        var entry = log[i];
        var eff = entry.effects || {};
        html += '<tr>' +
          '<td>' + esc(entry.case_name || '') + '</td>' +
          '<td>' + esc(entry.category  || '') + '</td>' +
          '<td>' + esc(entry.action    || '') + '</td>' +
          '<td>' + (eff.prestige  != null ? (eff.prestige  > 0 ? '+' : '') + eff.prestige  : '—') + '</td>' +
          '<td>' + (eff.treasury  != null ? (eff.treasury  > 0 ? '+' : '') + eff.treasury + '两' : '—') + '</td>' +
          '</tr>';
      }
      html += '</tbody></table>';
    }

    container.innerHTML = html;
  }

  window.Game.prefecture = {
    renderHeader:            renderPrefectureHeader,
    renderOverview:          renderPrefectureOverview,
    renderCounties:          renderPrefectureCounties,
    renderPersonnelTab:      renderPersonnelTab,
    openCountyDetail:        openPrefectureCountyDetail,
    showInspectResult:       showInspectResult,
    openQuotaModal:          openQuotaModal,
    collectQuotaAssignments: collectQuotaAssignments,
    updateQuotaRunningTotal: updateQuotaRunningTotal,
    openDirectiveModal:      openDirectiveModal,
    renderReport:            renderPrefectureReport,
    renderInfraPanel:        renderInfraPanel,
    buildPrefLogEntry:       buildPrefLogEntry,
    renderJudicialCase:      renderJudicialCase,
    renderJudicialResult:    renderJudicialResult,
    renderJudicialTab:       renderJudicialTab,
  };

})();
