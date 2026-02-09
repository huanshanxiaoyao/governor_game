/**
 * Screen & tab navigation — Game.screens namespace
 */
(function () {
  "use strict";

  window.Game = window.Game || {};

  function show(screenId) {
    var screens = document.querySelectorAll(".screen");
    for (var i = 0; i < screens.length; i++) {
      screens[i].classList.remove("active");
      screens[i].classList.add("hidden");
    }
    var target = document.getElementById(screenId);
    if (target) {
      target.classList.remove("hidden");
      target.classList.add("active");
    }
  }

  function showTab(tabId) {
    // Deactivate all tabs
    var tabs = document.querySelectorAll(".tab-content");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.remove("active");
      tabs[i].classList.add("hidden");
    }
    var btns = document.querySelectorAll(".tab-btn");
    for (var j = 0; j < btns.length; j++) {
      btns[j].classList.remove("active");
    }

    // Activate target
    var target = document.getElementById(tabId);
    if (target) {
      target.classList.remove("hidden");
      target.classList.add("active");
    }
    var activeBtn = document.querySelector('.tab-btn[data-tab="' + tabId + '"]');
    if (activeBtn) {
      activeBtn.classList.add("active");
    }
  }

  window.Game.screens = {
    show: show,
    showTab: showTab,
  };
})();
