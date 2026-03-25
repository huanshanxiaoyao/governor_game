/**
 * 书信系统 UI — Game.letter 命名空间（tab 嵌入式，非 modal）
 */
(function () {
  "use strict";

  var api = window.Game.api;
  var components = window.Game.components;

  function el(id) { return document.getElementById(id); }

  var _currentGameId = null;
  var _currentScreen = null; // 'county' | 'pref'
  var _tabState = { county: "inbox", pref: "inbox" };

  // -----------------------------------------------------------------------
  // 月份名映射
  // -----------------------------------------------------------------------
  var MONTH_NAMES = ["正月","二月","三月","四月","五月","六月",
                     "七月","八月","九月","十月","十一月","腊月"];
  function monthLabel(m) {
    if (!m) return "";
    var y = Math.ceil(m / 12);
    var mo = ((m - 1) % 12);
    return "第" + y + "年" + MONTH_NAMES[mo];
  }

  // -----------------------------------------------------------------------
  // 徽标更新
  // -----------------------------------------------------------------------
  function updateBadge(gameId) {
    api.getLetterSummary(gameId).then(function (data) {
      var count = (data.blocking_count || 0) + (data.unread_count || 0);
      ["letter-badge-county", "letter-badge-pref"].forEach(function (id) {
        var badge = el(id);
        if (!badge) return;
        if (count > 0) {
          badge.textContent = count > 9 ? "9+" : String(count);
          badge.classList.remove("hidden");
          if (data.blocking_count > 0) {
            badge.classList.add("letter-badge-urgent");
          } else {
            badge.classList.remove("letter-badge-urgent");
          }
        } else {
          badge.classList.add("hidden");
        }
      });
    }).catch(function () {});
  }

  // -----------------------------------------------------------------------
  // 从外部调用：激活信箱 tab 时载入内容
  // -----------------------------------------------------------------------
  function loadForScreen(screen, gameId) {
    _currentScreen = screen;
    _currentGameId = gameId;
    _switchTab(_tabState[screen] || "inbox");
  }

  // -----------------------------------------------------------------------
  // 内部 tab 切换
  // -----------------------------------------------------------------------
  function _switchTab(tab) {
    _tabState[_currentScreen] = tab;

    var listPane   = el(_currentScreen + "-letter-list");
    var detailPane = el(_currentScreen + "-letter-detail");
    if (!listPane) return;

    detailPane.classList.add("hidden");
    listPane.classList.remove("hidden");
    listPane.innerHTML = '<p class="letter-loading">加载中…</p>';

    // 高亮内层 tab 按钮
    var tabsEl = el(_currentScreen + "-letter-tabs");
    if (tabsEl) {
      tabsEl.querySelectorAll(".letter-tab-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.tab === tab);
      });
    }

    var fetcher;
    if (tab === "inbox")   fetcher = api.getLetterInbox(_currentGameId);
    else if (tab === "sent")    fetcher = api.getLetterSent(_currentGameId);
    else if (tab === "pending") fetcher = api.getLetterPending(_currentGameId);

    fetcher.then(function (data) {
      var letters = data.results || [];
      if (!letters.length) {
        listPane.innerHTML = '<p class="letter-empty">暂无信件</p>';
        return;
      }
      listPane.innerHTML = letters.map(renderLetterItem).join("");
      listPane.querySelectorAll(".letter-item").forEach(function (item) {
        item.addEventListener("click", function () {
          _openDetail(parseInt(item.dataset.id));
        });
      });
    }).catch(function (err) {
      listPane.innerHTML =
        '<p class="letter-error">加载失败：' + (err.message || "未知错误") + "</p>";
    });
  }

  // -----------------------------------------------------------------------
  // 信件列表项渲染
  // -----------------------------------------------------------------------
  var TYPE_LABELS = {
    OFFICIAL: "公文", PERSONAL: "私信", MEMORIAL: "奏折",
    INTELLIGENCE: "情报", CIRCULAR: "檄文"
  };
  var CONF_LABELS = {
    PUBLIC: "公开", PERSONAL: "个人", SECRET: "机密", BURN: "焚毁件"
  };

  function renderLetterItem(l) {
    var cls = "letter-item";
    if (l.status === "DELIVERED") cls += " letter-unread";
    if (l.is_blocking && l.requires_reply && (l.status === "DELIVERED" || l.status === "READ"))
      cls += " letter-blocking";

    var deadline = "";
    if (l.reply_deadline_month) {
      deadline = '<span class="letter-deadline">截止：' + monthLabel(l.reply_deadline_month) + "</span>";
    }
    var typeTag = '<span class="letter-tag letter-tag-type">' +
                  (TYPE_LABELS[l.letter_type] || l.letter_type) + "</span>";
    var confTag = l.confidentiality !== "PUBLIC" && l.confidentiality !== "PERSONAL"
      ? '<span class="letter-tag letter-tag-conf">' + (CONF_LABELS[l.confidentiality] || "") + "</span>"
      : "";
    var blockingTag = (l.is_blocking && l.requires_reply)
      ? '<span class="letter-tag letter-tag-urgent">紧急</span>' : "";
    var replyTag = (l.requires_reply && l.status !== "REPLIED")
      ? '<span class="letter-tag letter-tag-reply">待回复</span>' : "";

    var counterpart = l.player_is_sender ? l.recipient_name : l.sender_name;
    var dateLabel = l.player_is_sender
      ? "发出：" + monthLabel(l.sent_month)
      : "送达：" + monthLabel(l.delivered_month);

    return (
      '<div class="' + cls + '" data-id="' + l.id + '">' +
        '<div class="letter-item-header">' +
          '<span class="letter-counterpart">' + (counterpart || "—") + "</span>" +
          '<span class="letter-date">' + dateLabel + "</span>" +
        "</div>" +
        '<div class="letter-subject">' + l.subject + "</div>" +
        '<div class="letter-tags">' + typeTag + confTag + blockingTag + replyTag + deadline + "</div>" +
      "</div>"
    );
  }

  // -----------------------------------------------------------------------
  // 信件详情
  // -----------------------------------------------------------------------
  function _openDetail(letterId) {
    var listPane   = el(_currentScreen + "-letter-list");
    var detailPane = el(_currentScreen + "-letter-detail");
    if (!detailPane) return;

    listPane.classList.add("hidden");
    detailPane.classList.remove("hidden");
    detailPane.innerHTML = '<p class="letter-loading">加载中…</p>';

    api.getLetterDetail(_currentGameId, letterId).then(function (data) {
      detailPane.innerHTML = renderDetail(data);

      if (data.burned) {
        setTimeout(function () {
          components.showToast("此信已焚毁，不留存档", "info");
          _switchTab(_tabState[_currentScreen]);
        }, 2000);
        return;
      }

      var replyArea = detailPane.querySelector(".letter-reply-area");
      if (!replyArea) return;

      if (data.reply_options && data.reply_options.length) {
        detailPane.querySelectorAll(".letter-choice-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            _submitReply(letterId, btn.dataset.choice, null, btn);
          });
        });
      } else if (data.requires_reply && data.status !== "REPLIED") {
        var sendBtn = detailPane.querySelector("#letter-reply-send-btn");
        if (sendBtn) {
          sendBtn.addEventListener("click", function () {
            var body = detailPane.querySelector("#letter-reply-textarea").value.trim();
            if (!body) { components.showToast("请填写回复内容", "error"); return; }
            _submitReply(letterId, null, body, sendBtn);
          });
        }
      }

      var archBtn = detailPane.querySelector("#letter-archive-btn");
      if (archBtn) {
        archBtn.addEventListener("click", function () {
          api.archiveLetter(_currentGameId, letterId).then(function () {
            components.showToast("已归档", "success");
            _switchTab(_tabState[_currentScreen]);
          }).catch(function (err) {
            components.showToast(err.message || "归档失败", "error");
          });
        });
      }
    }).catch(function (err) {
      detailPane.innerHTML = '<p class="letter-error">加载失败：' + (err.message || "未知错误") + "</p>";
    });
  }

  function _submitReply(letterId, choiceId, body, btn) {
    if (btn) btn.disabled = true;
    var payload = {};
    if (choiceId) payload.choice_id = choiceId;
    if (body)     payload.body = body;
    api.replyLetter(_currentGameId, letterId, payload).then(function () {
      components.showToast("回复已发出", "success");
      updateBadge(_currentGameId);
      _switchTab(_tabState[_currentScreen]);
    }).catch(function (err) {
      components.showToast(err.message || "回复失败", "error");
      if (btn) btn.disabled = false;
    });
  }

  function renderDetail(d) {
    var sender    = d.player_is_sender ? "玩家（你）" : d.sender_name;
    var recipient = d.player_is_sender ? d.recipient_name : "玩家（你）";

    var metaHtml =
      '<div class="letter-detail-meta">' +
        '<div><span class="letter-meta-label">发件人：</span>' + sender + "</div>" +
        '<div><span class="letter-meta-label">收件人：</span>' + recipient + "</div>" +
        '<div><span class="letter-meta-label">发出：</span>' + monthLabel(d.sent_month) + "</div>" +
        (d.delivered_month
          ? '<div><span class="letter-meta-label">送达：</span>' + monthLabel(d.delivered_month) + "</div>"
          : "") +
        (d.reply_deadline_month
          ? '<div class="letter-deadline"><span class="letter-meta-label">截止回复：</span>' +
              monthLabel(d.reply_deadline_month) + "</div>"
          : "") +
      "</div>";

    var burnNotice = d.burned
      ? '<div class="letter-burn-notice">此信已焚毁，不留存档。</div>'
      : "";

    var parentHtml = "";
    if (d.parent_summary) {
      parentHtml =
        '<div class="letter-parent-ref">↩ 原信：' +
        d.parent_summary.subject + "（" + monthLabel(d.parent_summary.sent_month) + "）" +
        "</div>";
    }

    var replyHtml = "";
    if (d.status === "REPLIED") {
      var replyContent = d.reply_choice_id
        ? ("选项：" + d.reply_choice_id + (d.reply_body ? "；" + d.reply_body : ""))
        : d.reply_body;
      replyHtml =
        '<div class="letter-replied-notice">' +
          '<strong>已回复（' + monthLabel(d.replied_month) + '）：</strong><br>' +
          replyContent +
        "</div>";
    } else if (d.requires_reply) {
      if (d.reply_options && d.reply_options.length) {
        replyHtml =
          '<div class="letter-reply-area"><p class="letter-reply-label">请选择回复方式：</p>' +
          d.reply_options.map(function (opt) {
            return (
              '<button class="btn btn-small letter-choice-btn" data-choice="' + opt.id + '">' +
                opt.text +
                (opt.hint ? '<span class="letter-choice-hint"> — ' + opt.hint + "</span>" : "") +
              "</button>"
            );
          }).join("") +
          "</div>";
      } else {
        replyHtml =
          '<div class="letter-reply-area">' +
            '<p class="letter-reply-label">回复：</p>' +
            '<textarea id="letter-reply-textarea" rows="4" placeholder="书写回复…" ' +
              'style="width:100%;padding:8px;border:1px solid #c8b98a;border-radius:4px;' +
              'font-size:0.92em;resize:vertical;"></textarea>' +
            '<button id="letter-reply-send-btn" class="btn btn-primary btn-small" ' +
              'style="margin-top:8px;">发送回复</button>' +
          "</div>";
      }
    }

    var archiveBtn =
      (d.status === "DELIVERED" || d.status === "READ" || d.status === "REPLIED") && !d.burned
        ? '<button id="letter-archive-btn" class="btn btn-small" style="margin-top:10px;">归档</button>'
        : "";

    return (
      '<button id="btn-letter-back" class="btn btn-small letter-back-btn">← 返回列表</button>' +
      '<div class="letter-detail-card">' +
        '<h4 class="letter-detail-subject">' + d.subject + "</h4>" +
        metaHtml + parentHtml + burnNotice +
        '<div class="letter-detail-body">' + (d.body || "").replace(/\n/g, "<br>") + "</div>" +
        replyHtml + archiveBtn +
      "</div>"
    );
  }

  // -----------------------------------------------------------------------
  // 写信 Modal
  // -----------------------------------------------------------------------
  function openCompose(gameId, agents) {
    _currentGameId = gameId;
    var modal  = el("letter-compose-modal");
    var select = el("letter-compose-recipient");
    select.innerHTML = agents.map(function (a) {
      return '<option value="' + a.id + '">' + a.role_title + " " + a.name + "</option>";
    }).join("");
    el("letter-compose-subject").value = "";
    el("letter-compose-body").value = "";
    modal.classList.remove("hidden");
  }

  function closeCompose() {
    el("letter-compose-modal").classList.add("hidden");
  }

  function submitCompose() {
    var agentId = parseInt(el("letter-compose-recipient").value);
    var typeVal = el("letter-compose-type").value;
    var subject = el("letter-compose-subject").value.trim();
    var body    = el("letter-compose-body").value.trim();
    var btn     = el("btn-letter-compose-send");

    if (!subject || !body) {
      components.showToast("请填写主题和正文", "error");
      return;
    }
    btn.disabled = true;
    api.composeLetter(_currentGameId, {
      recipient_agent_id: agentId,
      letter_type: typeVal,
      confidentiality: "PERSONAL",
      subject: subject,
      body: body,
    }).then(function (data) {
      components.showToast(data.message || "信件已发出", "success");
      closeCompose();
      if (_currentScreen) _switchTab("sent");
      updateBadge(_currentGameId);
    }).catch(function (err) {
      components.showToast(err.message || "发送失败", "error");
      btn.disabled = false;
    });
  }

  // -----------------------------------------------------------------------
  // 推进前阻断检查（供 app.js 调用）
  // -----------------------------------------------------------------------
  function checkBlockingBeforeAdvance(gameId) {
    return api.getLetterBlockingCheck(gameId).then(function (data) {
      if (data.blocked) {
        return false;
      }
      return true;
    });
  }

  // -----------------------------------------------------------------------
  // 公开接口
  // -----------------------------------------------------------------------
  window.Game = window.Game || {};
  window.Game.letter = {
    loadForScreen: loadForScreen,
    openCompose:   openCompose,
    closeCompose:  closeCompose,
    submitCompose: submitCompose,
    updateBadge:   updateBadge,
    checkBlockingBeforeAdvance: checkBlockingBeforeAdvance,
    // 供 app.js _showLetterBlockingModal 使用
    openDetail: function (letterId) { _openDetail(letterId); },
  };

  // -----------------------------------------------------------------------
  // DOM Ready — 绑定静态按钮事件
  // -----------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {

    // 内层 tab 点击（县）
    var countyTabsEl = el("county-letter-tabs");
    if (countyTabsEl) {
      countyTabsEl.addEventListener("click", function (e) {
        var btn = e.target.closest(".letter-tab-btn");
        if (!btn || _currentScreen !== "county") return;
        _switchTab(btn.dataset.tab);
      });
    }

    // 内层 tab 点击（府）
    var prefTabsEl = el("pref-letter-tabs");
    if (prefTabsEl) {
      prefTabsEl.addEventListener("click", function (e) {
        var btn = e.target.closest(".letter-tab-btn");
        if (!btn || _currentScreen !== "pref") return;
        _switchTab(btn.dataset.tab);
      });
    }

    // 详情页"返回列表"按钮（事件委托）
    ["county-letter-detail", "pref-letter-detail"].forEach(function (paneId) {
      var pane = el(paneId);
      if (!pane) return;
      pane.addEventListener("click", function (e) {
        if (e.target && e.target.id === "btn-letter-back") {
          var screen = paneId.replace("-letter-detail", "");
          pane.classList.add("hidden");
          el(screen + "-letter-list").classList.remove("hidden");
        }
      });
    });

    // 写信 Modal
    var composeClose = el("btn-letter-compose-cancel");
    if (composeClose) composeClose.addEventListener("click", closeCompose);
    var composeModal = el("letter-compose-modal");
    if (composeModal) composeModal.addEventListener("click", function (e) {
      if (e.target === composeModal) closeCompose();
    });
    var composeSend = el("btn-letter-compose-send");
    if (composeSend) composeSend.addEventListener("click", submitCompose);

    // 阻断 Modal 关闭
    var blockingClose = el("btn-letter-blocking-close");
    if (blockingClose) blockingClose.addEventListener("click", function () {
      el("letter-blocking-modal").classList.add("hidden");
    });
  });
})();
