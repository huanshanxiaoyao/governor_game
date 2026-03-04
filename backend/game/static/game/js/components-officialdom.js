/**
 * 官场体系渲染组件 — 全国省/府层级展示
 */
(function () {
  "use strict";

  window.Game = window.Game || {};
  window.Game.officialdom = {
    loaded: false,

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

    reset: function () {
      this.loaded = false;
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
