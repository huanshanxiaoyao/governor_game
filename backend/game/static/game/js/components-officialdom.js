/**
 * 官场体系渲染组件 — 全国省/府层级展示
 */
(function () {
  "use strict";

  window.Game = window.Game || {};
  window.Game.officialdom = {
    loaded: false,
    careerLoaded: false,

    load: function (gameId) {
      var container = document.getElementById("officialdom-content");
      if (!container) return;
      container.innerHTML = '<p class="hint">加载中...</p>';

      Game.api.getOfficialdom(gameId).then(function (data) {
        Game.officialdom.loaded = true;
        if (!data.available) {
          container.innerHTML = '<p class="hint">本局游戏尚未生成官场数据</p>';
          return;
        }
        container.innerHTML = renderOfficialdom(data);
        bindProvinceToggles(container);
      }).catch(function (err) {
        container.innerHTML = '<p class="error-msg">加载失败: ' + (err.message || err) + '</p>';
      });
    },

    loadCareer: function (gameId) {
      var container = document.getElementById("career-content");
      if (!container) return;
      container.innerHTML = '<p class="hint">加载中...</p>';

      Game.api.getCareer(gameId).then(function (data) {
        Game.officialdom.careerLoaded = true;
        container.innerHTML = renderCareer(data);
      }).catch(function (err) {
        container.innerHTML = '<p class="error-msg">加载失败: ' + (err.message || err) + '</p>';
      });
    },

    reset: function () {
      this.loaded = false;
      this.careerLoaded = false;
    }
  };

  // ── 主渲染 ──

  function renderOfficialdom(data) {
    var html = [];

    // 1. 皇帝
    if (data.monarch && data.monarch.agent) {
      html.push(renderMonarch(data.monarch));
    }

    // 2. 内阁
    if (data.cabinet && data.cabinet.length > 0) {
      html.push('<div class="officialdom-section">');
      html.push('<h3 class="section-title">内阁</h3>');
      html.push('<div class="official-cards">');
      data.cabinet.forEach(function (a) { html.push(renderOfficialCard(a)); });
      html.push('</div></div>');
    }

    // 3. 六部
    if (data.ministries) {
      var ministryOrder = ["吏部", "户部", "礼部", "兵部", "刑部", "工部"];
      html.push('<div class="officialdom-section">');
      html.push('<h3 class="section-title">六部</h3>');
      html.push('<div class="ministries-grid">');
      ministryOrder.forEach(function (name) {
        var agents = data.ministries[name];
        if (agents && agents.length > 0) {
          html.push('<div class="ministry-group">');
          html.push('<h4 class="ministry-name">' + esc(name) + '</h4>');
          var shangshu = [];
          var shilang = [];
          agents.forEach(function (a) {
            if (a.role === 'MINISTER') shangshu.push(a);
            else shilang.push(a);
          });
          shangshu.forEach(function (a) { html.push(renderOfficialCard(a)); });
          if (shilang.length > 0) {
            html.push('<div class="shilang-group">');
            shilang.forEach(function (a) { html.push(renderOfficialCard(a, true)); });
            html.push('</div>');
          }
          html.push('</div>');
        }
      });
      html.push('</div></div>');
    }

    // 4. 都察院
    if (data.censorate && data.censorate.length > 0) {
      html.push(renderCensorate(data.censorate));
    }

    // 5. 全国地方官系 — 按省分组
    if (data.provinces) {
      html.push(renderProvinces(data.provinces, data.player_province));
    }

    // 6. 派系
    if (data.factions && data.factions.length > 0) {
      html.push(renderFactions(data.factions));
    }

    return html.join("");
  }

  // ── 局部渲染 ──

  function renderMonarch(monarch) {
    var a = monarch.agent;
    var ga = monarch.gameplay_attributes || {};
    var displayName = a.name;
    return (
      '<div class="officialdom-section monarch-section">' +
        '<div class="monarch-card">' +
          '<div class="monarch-badge">' + esc(monarch.archetype_display) + '</div>' +
          '<h2 class="monarch-name">' + esc(displayName) + '</h2>' +
          '<div class="monarch-title">皇帝</div>' +
          (ga.governing_style ? '<div class="monarch-style">执政风格: ' + esc(ga.governing_style) + '</div>' : '') +
          '<div class="monarch-stats">' +
            renderAttrBar("税收压力", ga.tax_pressure) +
            renderAttrBar("贪腐风险", ga.corruption_risk) +
            renderAttrBar("党争强度", ga.faction_intensity) +
            renderAttrBar("施政自由", ga.player_freedom) +
            renderAttrBar("任人唯贤", ga.meritocracy) +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function renderCensorate(agents) {
    var chief = [];
    var vice = [];
    var censors = [];
    agents.forEach(function (a) {
      if (a.role === 'CHIEF_CENSOR') chief.push(a);
      else if (a.role === 'VICE_CENSOR') vice.push(a);
      else censors.push(a);
    });

    var html = '<div class="officialdom-section">';
    html += '<h3 class="section-title">都察院</h3>';
    html += '<div class="censorate-tree">';

    chief.forEach(function (a) { html += renderOfficialCard(a); });

    if (vice.length > 0 || censors.length > 0) {
      html += '<div class="chain-arrow">↓</div>';
    }

    vice.forEach(function (a) { html += renderOfficialCard(a); });

    if (censors.length > 0) {
      html += '<div class="chain-arrow">↓</div>';
      html += '<div class="censors-row">';
      censors.forEach(function (a) { html += renderOfficialCard(a, true); });
      html += '</div>';
    }

    html += '</div></div>';
    return html;
  }

  function renderProvinces(provinces, playerProvince) {
    var html = '<div class="officialdom-section">';
    html += '<h3 class="section-title">全国地方官系 <span class="province-count">(' + Object.keys(provinces).length + '省)</span></h3>';

    var provNames = Object.keys(provinces);
    // 玩家所在省排在最前面
    if (playerProvince) {
      provNames.sort(function (a, b) {
        if (a === playerProvince) return -1;
        if (b === playerProvince) return 1;
        return 0;
      });
    }

    provNames.forEach(function (provName) {
      var prov = provinces[provName];
      var isPlayer = provName === playerProvince;
      var prefectCount = prov.prefects ? prov.prefects.length : 0;
      var expandedClass = isPlayer ? ' province-expanded' : '';
      var playerBadge = isPlayer ? ' <span class="player-province-badge">你在此省</span>' : '';

      html += '<div class="province-block' + expandedClass + '">';
      html += '<div class="province-header" data-province="' + esc(provName) + '">';
      html += '<span class="province-toggle">▶</span>';
      html += '<strong class="province-name">' + esc(provName) + '</strong>' + playerBadge;
      html += '<span class="province-meta">';
      if (prov.governor) {
        html += '巡抚: ' + esc(formatName(prov.governor.name, prov.governor.source_name));
      }
      html += ' · ' + prefectCount + '府';
      html += '</span>';
      html += '</div>';  // province-header

      html += '<div class="province-body">';

      // 巡抚
      if (prov.governor) {
        html += '<div class="prov-level prov-governor">';
        html += renderOfficialCard(prov.governor);
        html += '</div>';
      }

      // 布政使/按察使
      if (prov.commissioners && prov.commissioners.length > 0) {
        html += '<div class="chain-arrow">↓</div>';
        html += '<div class="prov-level commissioners-row">';
        prov.commissioners.forEach(function (a) { html += renderOfficialCard(a); });
        html += '</div>';
      }

      // 知府列表
      if (prov.prefects && prov.prefects.length > 0) {
        html += '<div class="chain-arrow">↓</div>';
        html += '<div class="prov-level prefects-grid">';
        prov.prefects.forEach(function (a) {
          html += renderOfficialCard(a, true);
        });
        // 如果是玩家所在省，显示知县标记
        if (isPlayer) {
          html += '<div class="official-card player-card official-card-compact"><div class="card-title">知县（你）</div></div>';
        }
        html += '</div>';
      }

      html += '</div>';  // province-body
      html += '</div>';  // province-block
    });

    html += '</div>';
    return html;
  }

  function renderOfficialCard(a, compact) {
    var displayName = formatName(a.name, a.source_name);
    var factionClass = a.faction_name ? ' faction-' + hashFaction(a.faction_name) : '';
    var compactClass = compact ? ' official-card-compact' : '';
    var rankText = a.rank ? a.rank + '品' : '';
    return (
      '<div class="official-card' + factionClass + compactClass + '">' +
        '<div class="card-title">' + esc(a.role_title) + '</div>' +
        '<div class="card-name">' + esc(displayName) + '</div>' +
        (rankText ? '<span class="rank-badge">' + rankText + '</span>' : '') +
        (a.faction_name ? '<span class="faction-tag">' + esc(a.faction_name) + '</span>' : '') +
      '</div>'
    );
  }

  function renderFactions(factions) {
    var html = '<div class="officialdom-section"><h3 class="section-title">朝廷派系</h3>';
    html += '<div class="faction-list">';
    factions.forEach(function (f) {
      var leaderDisplay = f.leader_name
        ? formatName(f.leader_name, f.leader_source_name)
        : '暂无';
      var desc = (f.ideology && f.ideology.description) || '';
      html += (
        '<div class="faction-card faction-' + hashFaction(f.name) + '">' +
          '<div class="faction-header">' +
            '<strong>' + esc(f.name) + '</strong>' +
            '<span class="faction-favor">圣眷 ' + f.imperial_favor + '</span>' +
          '</div>' +
          '<div class="faction-favor-bar"><div class="favor-fill" style="width:' + f.imperial_favor + '%"></div></div>' +
          '<div class="faction-meta">领袖: ' + esc(leaderDisplay) + ' · 成员 ' + (f.member_count || 0) + '人</div>' +
          (desc ? '<div class="faction-desc">' + esc(desc) + '</div>' : '') +
        '</div>'
      );
    });
    html += '</div></div>';
    return html;
  }

  // ── 省份折叠交互 ──

  function bindProvinceToggles(container) {
    var headers = container.querySelectorAll('.province-header');
    headers.forEach(function (header) {
      header.addEventListener('click', function () {
        var block = header.closest('.province-block');
        if (block) {
          block.classList.toggle('province-expanded');
        }
      });
    });
  }

  // ── 工具函数 ──

  function formatName(name, sourceName) {
    if (sourceName) return name + '(' + sourceName + ')';
    return name;
  }

  function renderAttrBar(label, value) {
    if (value === undefined || value === null) return '';
    var pct = Math.round(value * 100);
    return (
      '<div class="attr-row">' +
        '<span class="attr-label">' + label + '</span>' +
        '<div class="attr-bar"><div class="attr-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="attr-val">' + pct + '%</span>' +
      '</div>'
    );
  }

  // ── 仕途轨迹渲染 ─────────────────────────────────────────────────────────

  var POOL_BADGE_CLASS = ["pool-none", "pool-level1", "pool-level2", "pool-level3"];
  var GRADE_CLASS = {"优": "grade-you", "良": "grade-liang", "中": "grade-zhong", "差": "grade-cha"};

  // 供外部（app.js）调用：刷新仕途面板
  window.Game.officialdom.refreshCareer = function (gameId) {
    Game.officialdom.careerLoaded = false;
    Game.officialdom.loadCareer(gameId);
  };

  function renderCareer(data) {
    var html = [];
    html.push('<div class="career-wrap">');

    // ── 升迁事件（优先展示）──
    if (data.promotion_event) {
      html.push(renderPromotionEvent(data.promotion_event));
    }

    // ── 仕途概况栏 ──
    var level = data.candidate_pool_level || 0;
    var badgeCls = POOL_BADGE_CLASS[level] || "pool-none";
    var seasonsDone = (data.current_season || 1) - 1;
    var maxSeason = data.max_season || 36;
    var pct = Math.round(seasonsDone / maxSeason * 100);

    html.push('<div class="career-header">');
    html.push('<div class="career-stat"><span class="career-stat-label">品级</span><span class="career-stat-value">' + esc(data.rank || "七品") + '</span></div>');
    html.push('<div class="career-stat"><span class="career-stat-label">候选资格</span><span class="pool-badge ' + badgeCls + '">' + esc(data.pool_level_label || "未入候选池") + '</span></div>');
    html.push('<div class="career-stat"><span class="career-stat-label">第 ' + (data.term_index || 1) + ' 任</span><span class="career-stat-value">已任 ' + seasonsDone + ' 月 / 共 ' + maxSeason + ' 月</span></div>');
    html.push('</div>');

    // 任期进度条
    html.push('<div class="career-progress-bar"><div class="career-progress-fill" style="width:' + pct + '%"></div></div>');

    // ── 升迁条件说明 ──
    var req = data.promotion_requirements || {};
    html.push('<div class="career-requirements">');
    if (req.next_level_label) {
      html.push('<span class="career-req-title">下一目标：' + esc(req.next_level_label) + '</span>');
      if (req.seasons_remaining !== null && req.seasons_remaining !== undefined) {
        html.push('<span class="career-req-remain">（距任期届满还剩 ' + req.seasons_remaining + ' 月）</span>');
      }
    } else {
      html.push('<span class="career-req-title">已达最高阶段</span>');
    }
    html.push('<p class="career-req-desc">' + esc(req.description || "") + '</p>');
    html.push('</div>');

    // ── 进入候选池日志 ──
    var log = data.pool_entry_log || [];
    if (log.length > 0) {
      html.push('<div class="career-section">');
      html.push('<h4 class="career-section-title">候选池记录</h4>');
      html.push('<ul class="career-log-list">');
      log.forEach(function (entry) {
        html.push('<li class="career-log-item">' + esc(entry.note || "") + '</li>');
      });
      html.push('</ul></div>');
    }

    // ── 历年考评表（按任期分组）──
    var byTerm = data.annual_reviews_by_term || [];
    // 兼容旧数据：若无 by_term，降级展示平铺列表
    var allReviews = data.annual_reviews || [];
    html.push('<div class="career-section">');
    html.push('<h4 class="career-section-title">历年考评</h4>');
    var hasAny = byTerm.some(function (t) { return t.reviews && t.reviews.length > 0; });
    if (!hasAny && allReviews.length === 0) {
      html.push('<p class="hint">尚无已完成的年度考评</p>');
    } else if (byTerm.length > 0 && hasAny) {
      byTerm.forEach(function (term) {
        if (!term.reviews || term.reviews.length === 0) return;
        html.push('<div class="career-term-group">');
        html.push('<div class="career-term-label">第 ' + term.term_index + ' 任' +
          (term.county_name ? ' — ' + esc(term.county_name) : '') + '</div>');
        html.push('<table class="career-review-table">');
        html.push('<thead><tr><th>年份</th><th>客观评分</th><th>知府初评</th><th>巡抚最终</th><th>决定</th></tr></thead>');
        html.push('<tbody>');
        term.reviews.forEach(function (r) {
          var finalCls = GRADE_CLASS[r.final_grade] || "";
          var score = r.objective_score !== null && r.objective_score !== undefined
            ? r.objective_score.toFixed(1) : "—";
          html.push('<tr>');
          html.push('<td>第 ' + (r.year || "?") + ' 年</td>');
          html.push('<td>' + score + '</td>');
          html.push('<td>' + renderGradeBadge(r.prefect_grade) + '</td>');
          html.push('<td>' + renderGradeBadge(r.final_grade, finalCls) + '</td>');
          html.push('<td>' + esc(r.governor_decision || "—") + '</td>');
          html.push('</tr>');
          if (r.incident_flags && r.incident_flags.length > 0) {
            html.push('<tr class="career-incident-row"><td colspan="5">⚠ ' + r.incident_flags.map(esc).join('、') + '</td></tr>');
          }
        });
        html.push('</tbody></table>');
        html.push('</div>');
      });
    } else {
      // 降级：平铺
      html.push('<table class="career-review-table">');
      html.push('<thead><tr><th>年份</th><th>客观评分</th><th>知府初评</th><th>巡抚最终</th><th>决定</th></tr></thead>');
      html.push('<tbody>');
      allReviews.forEach(function (r) {
        var finalCls = GRADE_CLASS[r.final_grade] || "";
        var score = r.objective_score !== null && r.objective_score !== undefined
          ? r.objective_score.toFixed(1) : "—";
        html.push('<tr>');
        html.push('<td>第 ' + (r.year || "?") + ' 年</td>');
        html.push('<td>' + score + '</td>');
        html.push('<td>' + renderGradeBadge(r.prefect_grade) + '</td>');
        html.push('<td>' + renderGradeBadge(r.final_grade, finalCls) + '</td>');
        html.push('<td>' + esc(r.governor_decision || "—") + '</td>');
        html.push('</tr>');
        if (r.incident_flags && r.incident_flags.length > 0) {
          html.push('<tr class="career-incident-row"><td colspan="5">⚠ ' + r.incident_flags.map(esc).join('、') + '</td></tr>');
        }
      });
      html.push('</tbody></table>');
    }
    html.push('</div>');

    html.push('</div>'); // career-wrap
    return html.join("");
  }

  // ── 升迁事件渲染 ─────────────────────────────────────────────────────────

  var STATE_LABEL = {
    "governor_drafting":   "巡抚拟定名单中",
    "player_action_window":"行动窗口开放（截止二月末）",
    "ministry_submitted":  "提名已报吏部，等待裁决",
    "result_published":    "吏部已放榜"
  };

  var GIFT_LABEL = {
    "gift_governor": "送礼巡抚",
    "gift_ministry": "送礼吏部",
    "gift_both":     "两路疏通",
    "none":          "不送礼"
  };

  var RESULT_CLASS = {
    "promoted":            "promo-result-success",
    "rejected_by_ministry":"promo-result-fail",
    "not_nominated":       "promo-result-fail"
  };

  function renderPromotionEvent(ev) {
    var html = [];
    var state = ev.state || "";
    var stateLabel = STATE_LABEL[state] || state;

    html.push('<div class="promo-event-box">');
    html.push('<div class="promo-event-header">');
    html.push('<span class="promo-event-title">升迁提名事件</span>');
    html.push('<span class="promo-state-badge">' + esc(stateLabel) + '</span>');
    html.push('</div>');

    if (ev.vacancy_prefecture) {
      html.push('<p class="promo-vacancy">出缺府：<strong>' + esc(ev.vacancy_prefecture) + '知府</strong></p>');
    }

    // 候选人信息
    html.push('<div class="promo-candidates">');
    html.push('<span class="promo-candidates-label">候选人（共 ' + ev.candidates_count + ' 名）：</span>');
    (ev.candidates || []).forEach(function (c) {
      if (c.is_player) {
        html.push('<span class="promo-candidate self">本官（评分 ' + (c.base_score || '--') + '）</span>');
      } else if (state === "result_published") {
        var archLabel = {CORRUPT: "贪", MIDDLING: "平", VIRTUOUS: "廉"}[c.archetype] || "";
        var giftLabel = c.gift_decision && c.gift_decision !== "none" ? "（曾行贿）" : "";
        html.push('<span class="promo-candidate npc">' + esc(c.name) + archLabel + giftLabel + '</span>');
      } else {
        html.push('<span class="promo-candidate npc">竞争者</span>');
      }
    });
    html.push('</div>');

    // 行动窗口
    if (state === "player_action_window" && !ev.player_action) {
      html.push('<div class="promo-actions">');
      // 师爷建议
      if (!ev.advisor_tip_revealed) {
        html.push('<button class="promo-btn promo-btn-advisor" onclick="handlePromotionAdvisor()">问师爷</button>');
      } else if (ev.advisor_tip) {
        html.push('<div class="promo-advisor-tip"><strong>师爷：</strong>' + esc(ev.advisor_tip) + '</div>');
      }
      // 送礼选项
      var costs = ev.gift_costs || {};
      var opts = [
        {type: "gift_governor", label: "送礼巡抚", desc: "费" + (costs.gift_governor || 40) + "两，提名权重 ×1.5"},
        {type: "gift_ministry", label: "送礼吏部", desc: "费" + (costs.gift_ministry || 60) + "两，批复概率 +20%"},
        {type: "gift_both",     label: "两路疏通", desc: "费" + (costs.gift_both || 100) + "两，双重加成"},
        {type: "none",          label: "暂不行动", desc: "凭实绩正面应对"},
      ];
      html.push('<div class="promo-gift-grid">');
      opts.forEach(function (o) {
        html.push(
          '<div class="promo-gift-card" onclick="handlePromotionAction(\'' + o.type + '\')">' +
          '<div class="promo-gift-card-title">' + esc(o.label) + '</div>' +
          '<div class="promo-gift-card-desc">' + esc(o.desc) + '</div>' +
          '</div>'
        );
      });
      html.push('</div>');
      html.push('</div>'); // promo-actions
    }

    // 已行动提示
    if (ev.player_action !== null && ev.player_action !== undefined && state === "player_action_window") {
      html.push('<p class="promo-action-done">已选择：<strong>' + esc(GIFT_LABEL[ev.player_action] || ev.player_action) + '</strong>，等待二月末提交提名。</p>');
      if (ev.advisor_tip) {
        html.push('<div class="promo-advisor-tip"><strong>师爷：</strong>' + esc(ev.advisor_tip) + '</div>');
      }
    }

    // 等待吏部
    if (state === "ministry_submitted") {
      var nom = ev.nomination_result;
      if (nom === "player") {
        html.push('<p class="promo-ministry-msg promo-ministry-nominated">本官已获巡抚提名，等待吏部三月裁决。</p>');
      } else if (nom) {
        html.push('<p class="promo-ministry-msg promo-ministry-not-nominated">本官未获提名，三月将公布最终结果。</p>');
      } else {
        html.push('<p class="promo-ministry-msg">等待吏部三月裁决。</p>');
      }
    }

    // 结果公布
    if (state === "result_published" && ev.result) {
      var resCls = RESULT_CLASS[ev.result] || "";
      html.push('<div class="promo-result ' + resCls + '">');
      html.push('<p class="promo-result-reason">' + esc(ev.result_reason) + '</p>');
      if (ev.result === "promoted") {
        html.push('<button class="btn-primary promo-new-game-btn" onclick="handleStartPrefectGame()">就任知府，开启新局</button>');
      }
      html.push('</div>');
    }

    html.push('</div>'); // promo-event-box
    return html.join("");
  }

  function renderGradeBadge(grade, extraCls) {
    if (!grade) return '<span class="grade-empty">—</span>';
    var cls = GRADE_CLASS[grade] || "";
    if (extraCls) cls = extraCls;
    return '<span class="grade-badge ' + cls + '">' + esc(grade) + '</span>';
  }

  function hashFaction(name) {
    var h = 0;
    for (var i = 0; i < name.length; i++) {
      h = ((h << 5) - h) + name.charCodeAt(i);
      h |= 0;
    }
    return Math.abs(h) % 4;
  }

  function esc(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

})();
