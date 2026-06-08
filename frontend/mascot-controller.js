/* ============================================
   MascotController — 猫猫动画状态控制器
   管理 Lottie 播放、CSS 特效、叠加层
   ============================================ */
'use strict';

class MascotController {

  // ── 动作定义 ──────────────────────────────────────────────
  static ACTIONS = {

    idle: {
      name: 'Idle',
      css: '',
      speed: 1,
      activate(p) { p.play(); },
      fx: '',
    },

    wave: {
      name: 'Wave',
      css: 'm-wave',
      speed: 1.3,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-wave" style="top:26px;right:8px;">👋</div>
        <div class="fx-wave-toss" style="top:12px;right:22px;">❤️</div>
        <div class="fx-wave-toss" style="top:16px;right:40px;font-size:12px;animation-delay:0.5s;">✨</div>`,
    },

    think: {
      name: 'Think',
      css: 'm-think',
      speed: 0.4,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-ring" style="width:40px;height:40px;border-color:rgba(253,203,110,0.15);"></div>
        <div class="fx-ring" style="width:65px;height:65px;border-color:rgba(162,155,254,0.1);animation-delay:0.4s;"></div>
        <div class="fx-ring" style="width:90px;height:90px;border-color:rgba(116,185,255,0.07);animation-delay:0.8s;"></div>
        <div class="fx-float" style="top:12px;left:18px;font-size:22px;color:#fdca6e;animation-delay:0s;">?</div>
        <div class="fx-float" style="top:6px;left:34px;font-size:14px;color:#a29bfe;animation-delay:0.4s;">?</div>
        <div class="fx-float" style="top:0;left:48px;font-size:10px;color:#74b9ff;animation-delay:0.8s;">?</div>`,
    },

    sleep: {
      name: 'Sleep',
      css: 'm-sleep',
      speed: 0.12,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-float" style="top:4px;left:12px;font-size:18px;animation:sleep-glow 2s ease-in-out infinite;">🌙</div>
        <div class="fx-zzz" style="top:16px;right:14px;font-size:24px;">Z</div>
        <div class="fx-zzz" style="top:6px;right:28px;font-size:16px;color:#74b9ff;animation-delay:0.7s;">Z</div>
        <div class="fx-zzz" style="top:-2px;right:40px;font-size:11px;animation-delay:1.4s;">z</div>`,
    },

    happy: {
      name: 'Happy',
      css: 'm-happy',
      speed: 1.8,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-star" style="top:10px;left:10px;font-size:18px;color:#fdca6e;animation-delay:0s;">✦</div>
        <div class="fx-star" style="top:4px;right:14px;font-size:12px;color:#a29bfe;animation-delay:0.25s;">✦</div>
        <div class="fx-star" style="top:16px;right:4px;font-size:10px;color:#74b9ff;animation-delay:0.5s;">✦</div>
        <div class="fx-star" style="top:0;left:26px;font-size:8px;color:#ff7675;animation-delay:0.75s;">✦</div>
        <div class="fx-confetti" style="top:22px;left:12px;background:#fdca6e;animation-delay:0s;"></div>
        <div class="fx-confetti" style="top:18px;right:14px;background:#a29bfe;animation-delay:0.25s;"></div>
        <div class="fx-confetti" style="top:20px;left:45%;background:#74b9ff;animation-delay:0.5s;"></div>
        <div class="fx-confetti" style="top:24px;left:30%;background:#00b894;animation-delay:0.75s;"></div>`,
    },

    listen: {
      name: 'Listen',
      css: 'm-listen',
      speed: 1.2,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-sonar" style="width:30px;height:30px;border-color:rgba(162,155,254,0.3);"></div>
        <div class="fx-sonar" style="width:50px;height:50px;border-color:rgba(116,185,255,0.2);animation-delay:0.35s;"></div>
        <div class="fx-sonar" style="width:70px;height:70px;border-color:rgba(162,155,254,0.12);animation-delay:0.7s;"></div>
        <div class="fx-sonar" style="width:90px;height:90px;border-color:rgba(116,185,255,0.07);animation-delay:1.05s;"></div>
        <div class="fx-bob" style="top:22px;left:10px;font-size:18px;">👂</div>
        <div class="fx-bob" style="top:14px;left:22px;font-size:12px;animation-delay:0.12s;opacity:0.6;">🔊</div>
        <div class="fx-bob" style="top:6px;left:32px;font-size:8px;animation-delay:0.24s;opacity:0.3;">♪</div>`,
    },

    confuse: {
      name: 'Confuse',
      css: 'm-confuse',
      speed: 1,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-spiral" style="width:40px;height:40px;"></div>
        <div class="fx-pop" style="top:10px;right:12px;font-size:24px;color:#fdca6e;animation-delay:0s;">?</div>
        <div class="fx-pop" style="top:4px;right:26px;font-size:14px;color:#74b9ff;animation-delay:0.35s;">?</div>
        <div class="fx-pop" style="top:-2px;right:38px;font-size:9px;color:#a29bfe;animation-delay:0.7s;">?</div>`,
    },

    excited: {
      name: 'Excited',
      css: 'm-excited',
      speed: 2.0,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-glow-ring"></div>
        <div class="fx-burst" style="top:8px;left:8px;font-size:16px;color:#fdca6e;animation-delay:0s;">✦</div>
        <div class="fx-burst" style="top:2px;right:10px;font-size:12px;color:#a29bfe;animation-delay:0.12s;">✦</div>
        <div class="fx-burst" style="top:14px;right:4px;font-size:10px;color:#74b9ff;animation-delay:0.24s;">✦</div>
        <div class="fx-burst" style="bottom:24px;left:6px;font-size:8px;color:#ff7675;animation-delay:0.36s;">✦</div>
        <div class="fx-burst" style="top:0;left:26px;font-size:9px;color:#00b894;animation-delay:0.48s;">✦</div>`,
    },

    code: {
      name: 'Code',
      css: 'm-code',
      speed: 1.2,
      activate(p) { p.play(); },
      fx: `
        <div class="fx-beam" style="left:30%;animation-delay:0s;"></div>
        <div class="fx-beam" style="left:60%;animation-delay:0.25s;"></div>
        <div class="fx-code" style="top:14px;left:10px;font-size:12px;color:#fdca6e;animation-delay:0s;">&lt;/&gt;</div>
        <div class="fx-code" style="top:6px;right:12px;font-size:10px;color:#74b9ff;animation-delay:0.3s;">const</div>
        <div class="fx-code" style="top:16px;right:6px;font-size:8px;color:#00b894;animation-delay:0.6s;">=&gt;</div>
        <div class="fx-code" style="bottom:22px;left:6px;font-size:9px;color:#ff7675;animation-delay:0.9s;">fn()</div>
        <div class="fx-code" style="top:10px;left:26px;font-size:7px;color:#a29bfe;animation-delay:1.2s;">{...}</div>`,
    },
  };

  // ── 构造 ──────────────────────────────────────────────────
  constructor(playerEl, fxEl) {
    this.player = playerEl;
    this.fxEl = fxEl;
    this.current = 'idle';
    this._timer = null;
  }

  // ── 设为主动作 ──────────────────────────────────────────
  setAction(id) {
    const action = MascotController.ACTIONS[id];
    if (!action || this.current === id) return;
    this.current = id;

    // 清除旧特效
    this.fxEl.innerHTML = '';

    // 全重置播放器（seek(0) 比 stop() 更干净）
    try { this.player.seek(0); } catch(e) {}
    this.player.setSpeed(action.speed);
    this.player.setDirection(1);

    // 移除旧 CSS 类
    const ball = this.fxEl.parentElement;
    ball.className = ball.className
      .split(' ').filter(c => !c.startsWith('m-')).join(' ');

    // 激活新动作（仅控制播放，速度已在上面设好）
    action.activate(this.player);

    // 添加 CSS 类
    if (action.css) ball.classList.add(action.css);

    // 添加叠加层
    if (action.fx) this.fxEl.innerHTML = action.fx;
  }

  // ── 便捷状态方法 ──────────────────────────────────────
  onIdle()      { this.setAction('idle'); }
  onThinking()  { this.setAction('think'); }
  onSpeaking()  { this.setAction('wave'); }
  onHappy()     { this.setAction('happy'); }
  onError()     { this.setAction('confuse'); }
  onSleeping()  { this.setAction('sleep'); }
  onListening() { this.setAction('listen'); }
  onCoding()    { this.setAction('code'); }
  onExcited()   { this.setAction('excited'); }

  // ── 循环切换（供点击使用） ──────────────────────────
  cycle() {
    const ids = Object.keys(MascotController.ACTIONS);
    const idx = (ids.indexOf(this.current) + 1) % ids.length;
    this.setAction(ids[idx]);
  }
}
