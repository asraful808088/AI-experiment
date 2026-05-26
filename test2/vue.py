import ollama

prompt = '''



<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Collection — Supro Fashion Store</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #ffffff;
      --white: #ffffff;
      --off-white: #fff8f5;
      --black: #1a0a05;
      --mid: #9a8075;
      --light: #f0e0d8;
      --orange: #f07020;
      --orange-dark: #d05010;
      --orange-light: #ff9050;
      --crimson: #c01030;
      --crimson-dark: #8a0820;
      --crimson-light: #e02040;
      --sand: #fde8d8;
      --warm-light: #fff2ec;
      --text: #1a0a05;
      --serif: 'Cormorant Garamond', Georgia, serif;
      --sans: 'DM Sans', sans-serif;
    }
    html { scroll-behavior: smooth; }
    body { font-family: var(--sans); background: var(--white); color: var(--text); overflow-x: hidden; }

    /* ── NAV ── */
    nav {
      position: sticky; top: 0; z-index: 200;
      background: var(--white);
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 52px; height: 68px;
      border-bottom: 2px solid var(--light);
      box-shadow: 0 2px 24px rgba(192,16,48,.06);
    }
    .nav-logo { font-family: var(--serif); font-size: 1.8rem; font-weight: 600; letter-spacing: -0.5px; cursor: pointer; color: var(--black); text-decoration: none; }
    .nav-logo span { color: var(--crimson); }
    .nav-links { display: flex; gap: 32px; list-style: none; }
    .nav-links a { font-size: .8rem; letter-spacing: .1em; text-transform: uppercase; text-decoration: none; color: var(--mid); transition: color .2s; position: relative; }
    .nav-links a.active { color: var(--black); font-weight: 500; }
    .nav-links a.active::after { content:''; position:absolute; bottom:-6px; left:0; width:100%; height:2px; background: linear-gradient(90deg, var(--crimson), var(--orange)); }
    .nav-links a:hover { color: var(--crimson); }
    .nav-icons { display: flex; gap: 18px; align-items: center; }
    .nav-icons button { background: none; border: none; cursor: pointer; padding: 5px; color: var(--mid); transition: color .2s; position: relative; }
    .nav-icons button:hover { color: var(--crimson); }
    .nav-icons button.active-btn { color: var(--crimson); }
    .cart-badge { position: relative; }
    .cart-count { position: absolute; top: -6px; right: -8px; background: linear-gradient(135deg, var(--crimson), var(--orange)); color: #fff; font-size: .6rem; width: 16px; height: 16px; border-radius: 50%; display: flex; align-items: center; justify-content: center; transition: transform .2s; }
    .cart-count.bump { transform: scale(1.5); }

    /* ── DROPDOWNS & PANELS (same as main site) ── */
    .account-dropdown { position: fixed; top: 68px; right: 52px; width: 230px; background: var(--white); border: 1px solid var(--light); border-top: 3px solid var(--crimson); box-shadow: 0 12px 40px rgba(192,16,48,.12); opacity: 0; pointer-events: none; transform: translateY(-8px); transition: opacity .22s, transform .22s; z-index: 400; }
    .account-dropdown.open { opacity: 1; pointer-events: all; transform: translateY(0); }
    .acc-header { padding: 18px 20px; border-bottom: 1px solid var(--light); background: var(--warm-light); }
    .acc-header p { font-size: .78rem; color: var(--mid); margin-bottom: 2px; }
    .acc-header strong { font-size: .9rem; color: var(--black); font-weight: 500; }
    .acc-links { list-style: none; padding: 8px 0; }
    .acc-links li a { display: block; padding: 9px 20px; font-size: .82rem; color: var(--text); text-decoration: none; transition: all .15s; }
    .acc-links li a:hover { background: var(--warm-light); color: var(--crimson); padding-left: 26px; }
    .acc-divider { height: 1px; background: var(--light); margin: 4px 0; }
    .acc-signout { padding: 8px 20px 14px; }
    .acc-signout button { font-size: .75rem; letter-spacing: .1em; text-transform: uppercase; border: 1.5px solid var(--crimson); color: var(--crimson); padding: 8px 16px; background: none; cursor: pointer; width: 100%; transition: all .2s; font-family: var(--sans); }
    .acc-signout button:hover { background: var(--crimson); color: #fff; }
    .search-bar { position: fixed; top: 68px; left: 0; right: 0; z-index: 190; background: var(--white); border-bottom: 2px solid var(--orange); padding: 0 52px; height: 0; overflow: hidden; transition: height .3s ease; display: flex; align-items: center; gap: 14px; }
    .search-bar.open { height: 64px; }
    .search-bar input { flex: 1; border: none; outline: none; font-size: .95rem; font-family: var(--sans); color: var(--text); background: transparent; }
    .search-bar input::placeholder { color: var(--mid); }
    .search-bar button.search-submit { font-size: .72rem; letter-spacing: .12em; text-transform: uppercase; border: none; padding: 9px 22px; background: var(--orange); color: #fff; cursor: pointer; }
    .search-bar button.search-close { color: var(--mid); background: none; border: none; cursor: pointer; }
    .overlay { position: fixed; inset: 0; background: rgba(0,0,0,0); z-index: 180; pointer-events: none; transition: background .3s; }
    .overlay.active { background: rgba(26,10,5,.5); pointer-events: all; }
    .side-panel { position: fixed; top: 0; right: -440px; width: 440px; max-width: 100vw; height: 100vh; background: var(--white); z-index: 300; transition: right .35s cubic-bezier(.4,0,.2,1); display: flex; flex-direction: column; border-left: 3px solid var(--crimson); }
    .side-panel.open { right: 0; }
    .panel-header { display: flex; align-items: center; justify-content: space-between; padding: 22px 28px; border-bottom: 1px solid var(--light); background: linear-gradient(90deg, var(--warm-light), #fff); }
    .panel-header h2 { font-family: var(--serif); font-size: 1.4rem; font-weight: 400; color: var(--black); }
    .panel-header button { background: none; border: none; cursor: pointer; color: var(--mid); }
    .panel-header button:hover { color: var(--crimson); }
    .panel-body { flex: 1; overflow-y: auto; padding: 24px 28px; }
    .panel-footer { padding: 20px 28px; border-top: 1px solid var(--light); }
    .cart-item { display: flex; gap: 14px; padding: 16px 0; border-bottom: 1px solid var(--light); }
    .cart-item-img { width: 72px; height: 90px; background: var(--sand); flex-shrink: 0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
    .cart-item-img svg { width: 62px; height: 82px; }
    .cart-item-info { flex: 1; }
    .cart-item-name { font-size: .85rem; color: var(--black); margin-bottom: 4px; font-weight: 500; }
    .cart-item-meta { font-size: .73rem; color: var(--mid); margin-bottom: 10px; }
    .cart-item-row { display: flex; align-items: center; justify-content: space-between; }
    .qty-ctrl { display: flex; align-items: center; gap: 10px; }
    .qty-ctrl button { background: none; border: 1px solid var(--light); width: 26px; height: 26px; cursor: pointer; font-size: .9rem; display: flex; align-items: center; justify-content: center; transition: all .2s; }
    .qty-ctrl button:hover { border-color: var(--orange); color: var(--orange); }
    .qty-ctrl span { font-size: .85rem; min-width: 18px; text-align: center; font-weight: 500; }
    .cart-item-price { font-size: .88rem; font-weight: 500; color: var(--crimson); }
    .remove-btn { background: none; border: none; cursor: pointer; color: var(--mid); font-size: .73rem; margin-top: 6px; padding: 0; text-decoration: underline; }
    .remove-btn:hover { color: var(--crimson); }
    .cart-total { display: flex; justify-content: space-between; margin-bottom: 14px; font-size: .92rem; align-items: center; }
    .checkout-btn { width: 100%; padding: 15px; background: linear-gradient(90deg, var(--crimson), var(--orange)); color: #fff; border: none; font-family: var(--sans); font-size: .78rem; letter-spacing: .14em; text-transform: uppercase; cursor: pointer; transition: opacity .2s; }
    .checkout-btn:hover { opacity: .9; }
    .continue-btn { width: 100%; padding: 10px; background: none; color: var(--mid); border: 1px solid var(--light); font-family: var(--sans); font-size: .73rem; letter-spacing: .1em; text-transform: uppercase; cursor: pointer; margin-top: 8px; transition: all .2s; }
    .continue-btn:hover { color: var(--crimson); border-color: var(--crimson); }
    .wish-item { display: flex; gap: 14px; padding: 14px 0; border-bottom: 1px solid var(--light); align-items: center; }
    .wish-item-img { width: 62px; height: 78px; background: var(--sand); flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
    .wish-item-name { font-size: .85rem; color: var(--black); margin-bottom: 4px; font-weight: 500; }
    .wish-item-price { font-size: .8rem; color: var(--mid); margin-bottom: 8px; }
    .wish-add-btn { font-size: .72rem; letter-spacing: .1em; text-transform: uppercase; border: 1.5px solid var(--orange); color: var(--orange); padding: 6px 14px; background: none; cursor: pointer; font-family: var(--sans); transition: all .2s; }
    .wish-add-btn:hover { background: var(--orange); color: #fff; }
    .empty-msg { text-align: center; color: var(--mid); font-size: .9rem; padding: 52px 0; line-height: 2; }

    /* ── MOBILE DRAWER ── */
    .hamburger-btn { display: none; flex-direction: column; gap: 5px; cursor: pointer; padding: 8px 6px; background: none; border: none; }
    .hamburger-btn span { display: block; width: 22px; height: 1.5px; background: var(--text); border-radius: 2px; transition: all 0.35s cubic-bezier(.4,0,.2,1); transform-origin: center; }
    .hamburger-btn.open span:nth-child(1) { transform: rotate(45deg) translate(4.5px, 4.5px); background: var(--crimson); }
    .hamburger-btn.open span:nth-child(2) { opacity: 0; }
    .hamburger-btn.open span:nth-child(3) { transform: rotate(-45deg) translate(4.5px, -4.5px); background: var(--crimson); }
    .drawer-scrim { position: fixed; inset: 0; background: rgba(0,0,0,0); z-index: 295; pointer-events: none; transition: background 0.38s ease; }
    .drawer-scrim.active { background: rgba(26,10,5,0.55); pointer-events: all; }
    .mobile-drawer { position: fixed; top: 0; left: -320px; width: 300px; height: 100vh; background: var(--white); z-index: 299; transition: left 0.38s cubic-bezier(.4,0,.2,1); display: flex; flex-direction: column; border-right: 3px solid var(--crimson); }
    .mobile-drawer.open { left: 0; }
    .drawer-top { padding: 22px 20px 18px; border-bottom: 1px solid var(--light); flex-shrink: 0; background: var(--warm-light); }
    .drawer-brand { font-family: var(--serif); font-size: 1.6rem; font-weight: 600; letter-spacing: -0.5px; margin-bottom: 16px; display: block; color: var(--black); text-decoration: none; }
    .drawer-brand span { color: var(--crimson); }
    .drawer-user { display: flex; align-items: center; gap: 12px; background: #fff; border: 1px solid var(--light); border-radius: 10px; padding: 10px 12px; }
    .drawer-avatar { width: 38px; height: 38px; border-radius: 50%; background: linear-gradient(135deg, var(--crimson), var(--orange)); display: flex; align-items: center; justify-content: center; font-size: .72rem; font-weight: 500; color: #fff; flex-shrink: 0; }
    .drawer-user-name { font-size: .82rem; font-weight: 500; color: var(--black); }
    .drawer-signin-link { font-size: .72rem; color: var(--orange); text-decoration: none; font-weight: 500; margin-top: 3px; display: inline-block; }
    .drawer-body { flex: 1; overflow-y: auto; padding: 8px 0; }
    .drawer-section-label { font-size: .6rem; letter-spacing: .16em; text-transform: uppercase; color: var(--mid); padding: 14px 20px 6px; font-weight: 500; }
    .drawer-nav-item { display: flex; align-items: center; gap: 13px; padding: 12px 20px; cursor: pointer; text-decoration: none; color: var(--text); font-size: .85rem; border: none; background: none; width: 100%; font-family: var(--sans); transition: background .15s; position: relative; }
    .drawer-nav-item:hover { background: var(--warm-light); color: var(--crimson); }
    .drawer-nav-item.active { color: var(--crimson); font-weight: 500; }
    .drawer-nav-item.active::before { content:''; position:absolute; left:0; top:8px; bottom:8px; width:3px; background: linear-gradient(180deg, var(--crimson), var(--orange)); border-radius: 0 3px 3px 0; }
    .drawer-nav-item svg { flex-shrink: 0; opacity: 0.45; }
    .drawer-nav-item.active svg { opacity: 1; color: var(--crimson); }
    .drawer-nav-item-label { flex: 1; text-align: left; }
    .drawer-nav-badge { font-size: .6rem; font-weight: 500; background: var(--crimson); color: #fff; border-radius: 20px; padding: 2px 7px; }
    .drawer-nav-chip { font-size: .6rem; font-weight: 500; background: #fff2e8; color: var(--orange-dark); border-radius: 20px; padding: 2px 7px; border: 1px solid #ffd0b0; }
    .drawer-divider { height: 1px; background: var(--light); margin: 6px 0; }
    .drawer-bottom { padding: 16px 20px 20px; border-top: 1px solid var(--light); flex-shrink: 0; background: var(--warm-light); }
    .drawer-bottom-row { display: flex; gap: 10px; margin-bottom: 14px; }
    .drawer-bottom-btn { flex: 1; padding: 9px 8px; font-family: var(--sans); font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; cursor: pointer; border-radius: 3px; transition: all 0.2s; }
    .drawer-bottom-btn.outline { background: none; border: 1.5px solid var(--crimson); color: var(--crimson); }
    .drawer-bottom-btn.outline:hover { background: var(--crimson); color: #fff; }
    .drawer-bottom-btn.fill { background: var(--orange); border: 1.5px solid var(--orange); color: #fff; }
    .drawer-bottom-btn.fill:hover { background: var(--orange-dark); }
    .drawer-lang-row { display: flex; gap: 8px; }
    .drawer-select { font-size: .75rem; color: var(--mid); background: #fff; border: 1px solid var(--light); padding: 5px 8px; font-family: var(--sans); border-radius: 3px; cursor: pointer; flex: 1; }

    /* ── TOAST ── */
    .toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%) translateY(80px); background: linear-gradient(90deg, var(--crimson), var(--orange)); color: #fff; padding: 12px 28px; font-size: .82rem; letter-spacing: .05em; z-index: 500; transition: transform .3s ease; white-space: nowrap; box-shadow: 0 6px 24px rgba(192,16,48,.35); }
    .toast.show { transform: translateX(-50%) translateY(0); }

    /* ═══════════════════════════════════════
       COLLECTION PAGE STYLES
    ═══════════════════════════════════════ */

    /* ── HERO ── */
    .collection-hero {
      min-height: 540px;
      display: flex; align-items: center; justify-content: center;
      position: relative; overflow: hidden;
      background: var(--black);
    }
    .hero-bg-grad {
      position: absolute; inset: 0;
      background: linear-gradient(135deg, #1a0a05 0%, #3d1000 40%, #700818 75%, #b03010 100%);
    }
    /* diagonal light sweep */
    .hero-sweep {
      position: absolute; top: -40%; left: -20%;
      width: 60%; height: 200%;
      background: linear-gradient(120deg, transparent, rgba(240,112,32,0.07), transparent);
      transform: rotate(15deg);
      pointer-events: none;
    }
    .hero-pattern {
      position: absolute; inset: 0; opacity: .04;
      background-image: repeating-linear-gradient(45deg, #fff 0px, #fff 1px, transparent 1px, transparent 50px);
      background-size: 50px 50px;
    }
    .hero-rings {
      position: absolute; right: 8%; top: 50%; transform: translateY(-50%);
      width: 340px; height: 340px; pointer-events: none;
    }
    .hero-rings::before, .hero-rings::after { content:''; position:absolute; border-radius:50%; }
    .hero-rings::before { inset: 0; border: 1px solid rgba(240,112,32,0.2); }
    .hero-rings::after { inset: 40px; border: 1px solid rgba(192,16,48,0.15); }
    .hero-inner { position: relative; z-index: 2; text-align: center; max-width: 680px; padding: 0 24px; animation: heroFadeUp .8s ease both; }
    .hero-eyebrow { font-size: .68rem; letter-spacing: .28em; text-transform: uppercase; color: var(--orange); margin-bottom: 20px; display: flex; align-items: center; justify-content: center; gap: 12px; }
    .hero-eyebrow::before, .hero-eyebrow::after { content:''; width: 32px; height: 1px; background: var(--orange); opacity: .6; }
    .hero-title { font-family: var(--serif); font-size: clamp(3.2rem, 7vw, 6rem); font-weight: 300; line-height: 1.04; color: #fff; margin-bottom: 20px; }
    .hero-title em { color: var(--orange); font-style: italic; display: block; }
    .hero-desc { font-size: .88rem; color: rgba(255,255,255,0.5); max-width: 420px; margin: 0 auto 36px; line-height: 1.8; }
    .hero-ctas { display: flex; align-items: center; justify-content: center; gap: 16px; flex-wrap: wrap; }
    .btn-primary { font-size: .73rem; letter-spacing: .16em; text-transform: uppercase; padding: 14px 36px; background: var(--orange); color: #fff; border: none; cursor: pointer; font-family: var(--sans); transition: all .3s; text-decoration: none; display: inline-block; }
    .btn-primary:hover { background: var(--orange-dark); }
    .btn-ghost { font-size: .73rem; letter-spacing: .16em; text-transform: uppercase; padding: 13px 36px; background: none; color: rgba(255,255,255,0.8); border: 1px solid rgba(255,255,255,0.25); cursor: pointer; font-family: var(--sans); transition: all .3s; text-decoration: none; display: inline-block; }
    .btn-ghost:hover { border-color: var(--crimson); color: #fff; }
    .hero-scroll { position: absolute; bottom: 28px; left: 50%; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; gap: 6px; color: rgba(255,255,255,0.3); font-size: .6rem; letter-spacing: .18em; text-transform: uppercase; animation: bounce 2s ease-in-out infinite; }
    .hero-scroll svg { opacity: .4; }
    @keyframes bounce { 0%,100%{transform:translateX(-50%) translateY(0)} 50%{transform:translateX(-50%) translateY(6px)} }
    @keyframes heroFadeUp { from{opacity:0;transform:translateY(32px)} to{opacity:1;transform:translateY(0)} }

    /* ── SEASON STRIP ── */
    .season-strip {
      background: linear-gradient(90deg, var(--crimson-dark), var(--crimson), var(--orange), var(--orange-dark));
      padding: 0; overflow: hidden; height: 42px;
      display: flex; align-items: center;
    }
    .season-track {
      display: flex; gap: 0; white-space: nowrap;
      animation: marquee 22s linear infinite;
    }
    .season-track span {
      font-size: .63rem; letter-spacing: .2em; text-transform: uppercase;
      color: rgba(255,255,255,0.85); padding: 0 36px;
      border-right: 1px solid rgba(255,255,255,0.2);
      line-height: 42px;
    }
    .season-track span strong { color: #fff; font-weight: 600; }
    @keyframes marquee { from{transform:translateX(0)} to{transform:translateX(-50%)} }

    /* ── FEATURED COLLECTIONS GRID ── */
    .section { padding: 80px 52px; }
    .section-head { margin-bottom: 48px; }
    .section-eyebrow { font-size: .62rem; letter-spacing: .22em; text-transform: uppercase; color: var(--orange); margin-bottom: 10px; font-weight: 500; }
    .section-title { font-family: var(--serif); font-size: clamp(2rem, 4vw, 3rem); font-weight: 300; color: var(--black); line-height: 1.1; }
    .section-title em { color: var(--crimson); font-style: italic; }
    .section-sub { font-size: .82rem; color: var(--mid); margin-top: 10px; max-width: 460px; line-height: 1.7; }

    /* Masonry-style collection grid */
    .collection-grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      grid-template-rows: auto;
      gap: 16px;
    }
    .col-card {
      position: relative; overflow: hidden; cursor: pointer;
      background: var(--sand);
    }
    .col-card:nth-child(1) { grid-column: 1/6; grid-row: 1/3; min-height: 560px; }
    .col-card:nth-child(2) { grid-column: 6/10; grid-row: 1/2; min-height: 270px; }
    .col-card:nth-child(3) { grid-column: 10/13; grid-row: 1/2; min-height: 270px; }
    .col-card:nth-child(4) { grid-column: 6/9; grid-row: 2/3; min-height: 275px; }
    .col-card:nth-child(5) { grid-column: 9/13; grid-row: 2/3; min-height: 275px; }

    .col-card-bg {
      position: absolute; inset: 0;
      background-size: cover; background-position: center;
      transition: transform .7s cubic-bezier(.4,0,.2,1);
    }
    .col-card:hover .col-card-bg { transform: scale(1.06); }
    .col-card-overlay {
      position: absolute; inset: 0;
      background: linear-gradient(to top, rgba(26,10,5,0.85) 0%, rgba(26,10,5,0.2) 50%, transparent 100%);
      transition: background .4s;
    }
    .col-card:hover .col-card-overlay { background: linear-gradient(to top, rgba(26,10,5,0.92) 0%, rgba(26,10,5,0.35) 60%, transparent 100%); }
    .col-card-content {
      position: absolute; bottom: 0; left: 0; right: 0;
      padding: 28px 28px 32px;
      transform: translateY(8px);
      transition: transform .35s ease;
    }
    .col-card:hover .col-card-content { transform: translateY(0); }
    .col-tag { font-size: .58rem; letter-spacing: .18em; text-transform: uppercase; color: var(--orange); margin-bottom: 8px; font-weight: 500; }
    .col-name { font-family: var(--serif); font-size: 1.6rem; font-weight: 400; color: #fff; line-height: 1.15; margin-bottom: 6px; }
    .col-card:nth-child(1) .col-name { font-size: 2.2rem; }
    .col-count { font-size: .75rem; color: rgba(255,255,255,0.5); margin-bottom: 18px; }
    .col-cta {
      display: inline-flex; align-items: center; gap: 8px;
      font-size: .68rem; letter-spacing: .14em; text-transform: uppercase;
      color: #fff; text-decoration: none;
      opacity: 0; transform: translateY(6px);
      transition: opacity .3s .05s, transform .3s .05s, color .2s;
    }
    .col-card:hover .col-cta { opacity: 1; transform: translateY(0); }
    .col-cta:hover { color: var(--orange); }
    .col-cta svg { transition: transform .2s; }
    .col-cta:hover svg { transform: translateX(4px); }

    /* SVG illustrations for collection cards */
    .col-svg { width: 100%; height: 100%; position: absolute; inset: 0; }

    /* ── SEASON COLLECTIONS TABS ── */
    .season-section { background: var(--off-white); padding: 80px 52px; }
    .season-tabs { display: flex; gap: 0; border-bottom: 2px solid var(--light); margin-bottom: 48px; overflow-x: auto; }
    .season-tab {
      font-family: var(--serif); font-size: 1.15rem; font-weight: 400;
      cursor: pointer; color: var(--mid); border: none; background: none;
      padding: 14px 32px; transition: color .2s; white-space: nowrap;
      position: relative;
    }
    .season-tab::after { content:''; position:absolute; bottom:-2px; left:0; width:0; height:2px; background: linear-gradient(90deg,var(--crimson),var(--orange)); transition:width .3s; }
    .season-tab.active { color: var(--black); font-weight: 600; }
    .season-tab.active::after { width: 100%; }
    .season-tab:hover { color: var(--crimson); }
    .season-tab:hover::after { width: 100%; }

    .season-content { display: none; }
    .season-content.active { display: block; animation: fadeIn .4s ease; }
    @keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }

    .season-products { display: grid; grid-template-columns: repeat(4, 1fr); gap: 24px; }
    .prod-card { cursor: pointer; }
    .prod-img { position: relative; background: var(--sand); aspect-ratio: 3/4; overflow: hidden; margin-bottom: 14px; }
    .prod-img svg { width: 100%; height: 100%; transition: transform .5s ease; }
    .prod-card:hover .prod-img svg { transform: scale(1.05); }
    .prod-badge { position: absolute; top: 10px; left: 10px; font-size: .58rem; letter-spacing: .1em; text-transform: uppercase; padding: 3px 8px; font-weight: 500; }
    .badge-hot { background: var(--crimson); color: #fff; }
    .badge-sale { background: var(--orange); color: #fff; }
    .badge-new { background: var(--black); color: #fff; }
    .prod-wish { position: absolute; top: 10px; right: 10px; background: rgba(255,255,255,0.9); border: none; cursor: pointer; color: var(--mid); opacity: 0; transition: opacity .2s, color .2s; padding: 6px; width: 30px; height: 30px; display: flex; align-items: center; justify-content: center; }
    .prod-card:hover .prod-wish { opacity: 1; }
    .prod-wish.wished { color: var(--crimson); opacity: 1; }
    .prod-wish:hover { color: var(--crimson) !important; }
    .prod-quick { position: absolute; bottom: 0; left: 0; right: 0; background: rgba(26,10,5,0.88); color: #fff; border: none; font-family: var(--sans); font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; padding: 10px; cursor: pointer; opacity: 0; transform: translateY(4px); transition: opacity .25s, transform .25s; }
    .prod-card:hover .prod-quick { opacity: 1; transform: translateY(0); }
    .prod-quick:hover { background: var(--crimson); }
    .prod-name { font-size: .84rem; color: var(--black); margin-bottom: 5px; }
    .prod-stars { display: flex; gap: 2px; margin-bottom: 5px; }
    .prod-star { color: var(--orange); font-size: .68rem; }
    .prod-star.empty { color: var(--light); }
    .prod-price { font-size: .84rem; }
    .prod-price .old { color: var(--mid); text-decoration: line-through; margin-right: 6px; font-size: .76rem; }
    .prod-price .cur { color: var(--crimson); font-weight: 500; }

    /* ── DESIGNER SPOTLIGHT ── */
    .spotlight-section { padding: 80px 52px; background: var(--black); }
    .spotlight-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
    .spotlight-left { padding-right: 60px; display: flex; flex-direction: column; justify-content: center; }
    .spotlight-eyebrow { font-size: .62rem; letter-spacing: .22em; text-transform: uppercase; color: var(--orange); margin-bottom: 16px; }
    .spotlight-title { font-family: var(--serif); font-size: clamp(2.2rem, 4vw, 3.6rem); font-weight: 300; color: #fff; line-height: 1.08; margin-bottom: 20px; }
    .spotlight-title em { color: var(--orange); font-style: italic; }
    .spotlight-body { font-size: .85rem; color: rgba(255,255,255,0.45); line-height: 1.9; margin-bottom: 36px; }
    .spotlight-stats { display: grid; grid-template-columns: repeat(3,1fr); gap: 20px; margin-bottom: 40px; padding: 24px; border: 1px solid rgba(255,255,255,0.08); }
    .spot-stat .num { font-family: var(--serif); font-size: 2rem; font-weight: 600; color: var(--orange); display: block; line-height: 1; }
    .spot-stat .lbl { font-size: .62rem; letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,0.3); margin-top: 4px; display: block; }
    .spotlight-right {
      display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
    }
    .spot-img {
      background: var(--sand); position: relative; overflow: hidden;
      aspect-ratio: 3/4;
    }
    .spot-img:first-child { aspect-ratio: unset; grid-row: 1/3; }
    .spot-img svg { width: 100%; height: 100%; }
    .spot-img-label {
      position: absolute; bottom: 0; left: 0; right: 0;
      padding: 10px 14px;
      background: linear-gradient(to top, rgba(26,10,5,0.8), transparent);
      font-size: .7rem; color: rgba(255,255,255,0.8); letter-spacing: .06em;
    }

    /* ── LOOKBOOK BANNER ── */
    .lookbook-banner {
      margin: 0 52px 80px;
      background: linear-gradient(110deg, var(--crimson-dark) 0%, var(--crimson) 45%, var(--orange) 100%);
      padding: 60px 64px;
      display: flex; align-items: center; justify-content: space-between;
      position: relative; overflow: hidden;
      gap: 40px;
    }
    .lookbook-banner::before { content:''; position:absolute; right:-80px; top:-80px; width:360px; height:360px; border:1px solid rgba(255,255,255,0.1); border-radius:50%; pointer-events:none; }
    .lookbook-banner::after { content:''; position:absolute; right:20px; top:20px; width:220px; height:220px; border:1px solid rgba(255,255,255,0.08); border-radius:50%; pointer-events:none; }
    .lookbook-text { z-index:1; }
    .lookbook-tag { font-size:.62rem; letter-spacing:.22em; text-transform:uppercase; color:rgba(255,255,255,0.7); margin-bottom:12px; }
    .lookbook-title { font-family:var(--serif); font-size:clamp(1.8rem,3.5vw,3rem); font-weight:300; color:#fff; line-height:1.1; margin-bottom:12px; }
    .lookbook-sub { font-size:.82rem; color:rgba(255,255,255,0.6); line-height:1.7; max-width:360px; }
    .lookbook-actions { display:flex; gap:12px; z-index:1; flex-wrap:wrap; }
    .btn-white { font-size:.72rem; letter-spacing:.16em; text-transform:uppercase; padding:13px 30px; background:#fff; color:var(--crimson); border:none; cursor:pointer; font-family:var(--sans); transition:all .3s; text-decoration:none; display:inline-block; white-space: nowrap; }
    .btn-white:hover { background:var(--off-white); }
    .btn-outline-white { font-size:.72rem; letter-spacing:.16em; text-transform:uppercase; padding:12px 30px; background:none; color:#fff; border:1px solid rgba(255,255,255,0.45); cursor:pointer; font-family:var(--sans); transition:all .3s; text-decoration:none; display:inline-block; white-space: nowrap; }
    .btn-outline-white:hover { border-color:#fff; }

    /* ── TREND CARDS ── */
    .trends-section { padding: 0 52px 80px; }
    .trends-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
    .trend-card {
      position: relative; overflow: hidden;
      min-height: 340px; cursor: pointer;
    }
    .trend-bg { position: absolute; inset: 0; transition: transform .6s cubic-bezier(.4,0,.2,1); }
    .trend-card:hover .trend-bg { transform: scale(1.06); }
    .trend-overlay { position: absolute; inset: 0; background: linear-gradient(to top, rgba(26,10,5,0.9) 0%, rgba(26,10,5,0.1) 60%, transparent 100%); }
    .trend-content { position: absolute; bottom: 0; left: 0; right: 0; padding: 28px; }
    .trend-num { font-family: var(--serif); font-size: 4rem; font-weight: 300; color: rgba(255,255,255,0.12); line-height: 1; margin-bottom: -10px; display: block; }
    .trend-name { font-family: var(--serif); font-size: 1.5rem; font-weight: 400; color: #fff; line-height: 1.2; margin-bottom: 6px; }
    .trend-count { font-size: .72rem; color: rgba(255,255,255,0.5); letter-spacing: .06em; }
    .trend-arrow { position: absolute; top: 20px; right: 20px; width: 36px; height: 36px; border: 1px solid rgba(255,255,255,0.2); display: flex; align-items: center; justify-content: center; color: rgba(255,255,255,0.5); opacity: 0; transform: translateY(-4px); transition: all .3s; }
    .trend-card:hover .trend-arrow { opacity: 1; transform: translateY(0); border-color: var(--orange); color: var(--orange); }

    /* ── NEWSLETTER ── */
    .newsletter { padding: 80px 52px; background: var(--off-white); border-top: 1px solid var(--light); }
    .newsletter-inner { max-width: 520px; margin: 0 auto; text-align: center; }
    .newsletter-eyebrow { font-size: .62rem; letter-spacing: .22em; text-transform: uppercase; color: var(--crimson); margin-bottom: 14px; }
    .newsletter-title { font-family: var(--serif); font-size: 2.2rem; font-weight: 300; color: var(--black); margin-bottom: 12px; }
    .newsletter-sub { font-size: .82rem; color: var(--mid); line-height: 1.7; margin-bottom: 32px; }
    .newsletter-form { display: flex; gap: 0; }
    .newsletter-input { flex: 1; border: 2px solid var(--light); border-right: none; padding: 13px 18px; font-size: .85rem; font-family: var(--sans); color: var(--text); background: #fff; outline: none; transition: border-color .2s; }
    .newsletter-input:focus { border-color: var(--crimson); }
    .newsletter-btn { padding: 13px 28px; background: linear-gradient(90deg, var(--crimson), var(--orange)); color: #fff; border: none; font-family: var(--sans); font-size: .73rem; letter-spacing: .14em; text-transform: uppercase; cursor: pointer; white-space: nowrap; transition: opacity .2s; }
    .newsletter-btn:hover { opacity: .9; }

    /* ── FOOTER ── */
    footer { background: var(--black); padding: 64px 52px 40px; border-top: 3px solid var(--crimson); }
    .footer-brand { font-family: var(--serif); font-size: 2rem; font-weight: 600; color: #fff; margin-bottom: 8px; }
    .footer-brand span { color: var(--crimson); }
    .footer-tagline { font-size: .75rem; color: rgba(255,255,255,0.3); letter-spacing: .12em; text-transform: uppercase; margin-bottom: 40px; }
    .footer-grid { display:grid; grid-template-columns: 2fr repeat(4,1fr); gap:40px; margin-bottom:48px; }
    .footer-col h4 { font-size:.62rem; letter-spacing:.18em; text-transform:uppercase; color: var(--orange); font-weight:500; margin-bottom:20px; }
    .footer-col ul { list-style:none; display:flex; flex-direction:column; gap:12px; }
    .footer-col li a { font-size:.8rem; color:rgba(255,255,255,0.4); text-decoration:none; transition:color .2s; cursor:pointer; }
    .footer-col li a:hover { color: var(--orange); }
    .footer-select { font-size:.8rem; color:rgba(255,255,255,0.4); background:none; border:none; cursor:pointer; font-family:var(--sans); }
    .footer-bottom { display:flex; justify-content:space-between; align-items:center; padding-top:24px; border-top:1px solid rgba(255,255,255,0.08); }
    .footer-bottom p { font-size:.73rem; color:rgba(255,255,255,0.25); }
    .footer-bottom a { font-size:.73rem; color:rgba(255,255,255,0.25); text-decoration:none; margin-left:24px; transition: color .2s; }
    .footer-bottom a:hover { color: var(--orange); }
    .back-top { width:38px; height:38px; border:1px solid rgba(255,255,255,0.15); background:none; cursor:pointer; display:flex; align-items:center; justify-content:center; color:rgba(255,255,255,0.4); transition: all .2s; }
    .back-top:hover { background: var(--crimson); border-color: var(--crimson); color: #fff; }

    /* ── RESPONSIVE ── */
    @media (max-width: 1100px) {
      .col-card:nth-child(1) { grid-column: 1/7; }
      .col-card:nth-child(2) { grid-column: 7/10; }
      .col-card:nth-child(3) { grid-column: 10/13; }
      .col-card:nth-child(4) { grid-column: 7/10; }
      .col-card:nth-child(5) { grid-column: 10/13; }
      .season-products { grid-template-columns: repeat(3,1fr); }
      .section, .season-section, .spotlight-section, .trends-section, .newsletter { padding-left: 28px; padding-right: 28px; }
      .lookbook-banner { margin: 0 28px 60px; padding: 44px 40px; }
    }
    @media (max-width: 900px) {
      nav { padding: 0 24px; }
      .collection-grid { grid-template-columns: 1fr 1fr; }
      .col-card:nth-child(1) { grid-column: 1/3; grid-row: 1/2; min-height: 320px; }
      .col-card:nth-child(2) { grid-column: 1/2; grid-row: 2/3; }
      .col-card:nth-child(3) { grid-column: 2/3; grid-row: 2/3; }
      .col-card:nth-child(4) { grid-column: 1/2; grid-row: 3/4; }
      .col-card:nth-child(5) { grid-column: 2/3; grid-row: 3/4; }
      .spotlight-grid { grid-template-columns: 1fr; }
      .spotlight-left { padding-right: 0; padding-bottom: 40px; }
      .trends-row { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 768px) {
      .nav-links { display: none; }
      .hamburger-btn { display: flex; }
      .footer-grid { grid-template-columns: repeat(2,1fr); }
      footer { padding: 40px 24px 24px; }
      .account-dropdown { right: 8px; width: 210px; }
      .season-products { grid-template-columns: repeat(2,1fr); }
      .trends-row { grid-template-columns: 1fr; }
      .lookbook-banner { flex-direction: column; margin: 0 0 60px; }
      .newsletter-form { flex-direction: column; }
      .newsletter-input { border-right: 2px solid var(--light); border-bottom: none; }
    }
    @media (max-width: 480px) {
      .collection-grid { grid-template-columns: 1fr; }
      .col-card { grid-column: 1/2 !important; grid-row: auto !important; min-height: 260px !important; }
    }
  </style>
</head>
<body>

<div class="toast" id="toast"></div>
<div class="overlay" id="overlay" onclick="closeAll()"></div>
<div class="drawer-scrim" id="drawerScrim" onclick="closeMobileDrawer()"></div>

<!-- MOBILE DRAWER -->
<nav class="mobile-drawer" id="mobileDrawer">
  <div class="drawer-top">
    <a href="index.html" class="drawer-brand">Supro<span>.</span></a>
    <div class="drawer-user">
      <div class="drawer-avatar">GU</div>
      <div>
        <div class="drawer-user-name">Guest User</div>
        <a href="#" class="drawer-signin-link">Sign in / Register →</a>
      </div>
    </div>
  </div>
  <div class="drawer-body">
    <div class="drawer-section-label">Main</div>
    <button class="drawer-nav-item" onclick="setDrawerActive(this);closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
      <span class="drawer-nav-item-label">Home</span>
    </button>
    <button class="drawer-nav-item" onclick="setDrawerActive(this);closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><rect x="2" y="3" width="7" height="7"/><rect x="15" y="3" width="7" height="7"/><rect x="15" y="15" width="7" height="7"/><rect x="2" y="15" width="7" height="7"/></svg>
      <span class="drawer-nav-item-label">Shop All</span>
      <span class="drawer-nav-chip">New</span>
    </button>
    <button class="drawer-nav-item active" onclick="setDrawerActive(this);closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
      <span class="drawer-nav-item-label">Collection</span>
    </button>
    <button class="drawer-nav-item" onclick="setDrawerActive(this);openWishlist();closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
      <span class="drawer-nav-item-label">Wishlist</span>
      <span class="drawer-nav-badge" id="mobileWishBadge">0</span>
    </button>
    <div class="drawer-divider"></div>
    <div class="drawer-section-label">Categories</div>
    <button class="drawer-nav-item" onclick="setDrawerActive(this);closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M20.38 3.46L16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.57a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.57a2 2 0 0 0-1.34-2.23z"/></svg>
      <span class="drawer-nav-item-label">Clothing</span>
    </button>
    <button class="drawer-nav-item" onclick="setDrawerActive(this);closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
      <span class="drawer-nav-item-label">Bags & Accessories</span>
    </button>
    <button class="drawer-nav-item" onclick="setDrawerActive(this);closeMobileDrawer()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>
      <span class="drawer-nav-item-label">Shoes</span>
    </button>
  </div>
  <div class="drawer-bottom">
    <div class="drawer-bottom-row">
      <button class="drawer-bottom-btn outline" onclick="showToast('Sign in coming soon')">Sign In</button>
      <button class="drawer-bottom-btn fill" onclick="showToast('Register coming soon')">Register</button>
    </div>
    <div class="drawer-lang-row">
      <select class="drawer-select"><option>🌐 English</option><option>Français</option><option>Deutsch</option></select>
      <select class="drawer-select"><option>£ GBP</option><option>$ USD</option><option>€ EUR</option><option>৳ BDT</option></select>
    </div>
  </div>
</nav>

<!-- ACCOUNT DROPDOWN -->
<div class="account-dropdown" id="accountDropdown">
  <div class="acc-header"><p>Welcome back,</p><strong>Guest User</strong></div>
  <ul class="acc-links">
    <li><a href="#">My Account</a></li>
    <li><a href="#">Orders</a></li>
    <li><a href="#">Wishlist</a></li>
    <li><a href="#">Returns</a></li>
    <div class="acc-divider"></div>
    <li><a href="#">Settings</a></li>
  </ul>
  <div class="acc-signout"><button onclick="showToast('Signed out successfully')">Sign Out</button></div>
</div>

<!-- TOP NAV -->
<nav>
  <a href="index.html" class="nav-logo">Supro<span>.</span></a>
  <ul class="nav-links">
    <li><a href="index.html">Home</a></li>
    <li><a href="shop.html">Shop</a></li>
    <li><a href="collection.html" class="active">Collection</a></li>
    <li><a href="#">Pages</a></li>
    <li><a href="#">Blog</a></li>
    <li><a href="#">Contact</a></li>
  </ul>
  <div class="nav-icons">
    <button title="Search" id="searchBtn" onclick="toggleSearch()">
      <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    </button>
    <button title="Account" id="accountBtn" onclick="toggleAccount(event)">
      <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
    </button>
    <button title="Wishlist" id="wishlistBtn" onclick="openWishlist()">
      <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
    </button>
    <button title="Cart" id="cartBtn" class="cart-badge" onclick="openCart()">
      <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>
      <span class="cart-count" id="cartCount">0</span>
    </button>
    <button class="hamburger-btn" id="hamburgerBtn" title="Menu" onclick="toggleMobileDrawer()">
      <span></span><span></span><span></span>
    </button>
  </div>
</nav>

<!-- SEARCH BAR -->
<div class="search-bar" id="searchBar">
  <svg width="18" height="18" fill="none" stroke="var(--orange)" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
  <input type="text" id="searchInput" placeholder="Search collections, styles..." onkeydown="handleSearchKey(event)" />
  <button class="search-submit" onclick="doSearch()">Search</button>
  <button class="search-close" onclick="toggleSearch()">
    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>
  </button>
</div>

<!-- CART PANEL -->
<div class="side-panel" id="cartPanel">
  <div class="panel-header"><h2>Shopping Cart</h2><button onclick="closeCart()"><svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
  <div class="panel-body" id="cartBody"></div>
  <div class="panel-footer" id="cartFooter"></div>
</div>

<!-- WISHLIST PANEL -->
<div class="side-panel" id="wishlistPanel">
  <div class="panel-header"><h2>My Wishlist</h2><button onclick="closeWishlist()"><svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
  <div class="panel-body" id="wishBody"></div>
</div>

<!-- ═══ COLLECTION HERO ═══ -->
<section class="collection-hero">
  <div class="hero-bg-grad"></div>
  <div class="hero-sweep"></div>
  <div class="hero-pattern"></div>
  <div class="hero-rings"></div>
  <div class="hero-inner">
    <div class="hero-eyebrow">SS 2025 — Supro Fashion</div>
    <h1 class="hero-title">
      The Art of<br>
      <em>Dressing Well</em>
    </h1>
    <p class="hero-desc">Curated collections that speak the language of confidence. Each piece crafted to move with you, season after season.</p>
    <div class="hero-ctas">
      <a href="shop.html" class="btn-primary">Explore All</a>
      <a href="#featured" class="btn-ghost">View Lookbook</a>
    </div>
  </div>
  <div class="hero-scroll">
    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M12 5v14M5 12l7 7 7-7"/></svg>
    Scroll
  </div>
</section>

<!-- ═══ MARQUEE STRIP ═══ -->
<div class="season-strip">
  <div class="season-track">
    <span>✦ <strong>Spring / Summer 2025</strong></span>
    <span>✦ New Arrivals Weekly</span>
    <span>✦ <strong>Free Returns</strong> within 30 days</span>
    <span>✦ Members get <strong>15% Off</strong></span>
    <span>✦ <strong>Spring / Summer 2025</strong></span>
    <span>✦ New Arrivals Weekly</span>
    <span>✦ <strong>Free Returns</strong> within 30 days</span>
    <span>✦ Members get <strong>15% Off</strong></span>
    <!-- duplicate for seamless loop -->
    <span>✦ <strong>Spring / Summer 2025</strong></span>
    <span>✦ New Arrivals Weekly</span>
    <span>✦ <strong>Free Returns</strong> within 30 days</span>
    <span>✦ Members get <strong>15% Off</strong></span>
    <span>✦ <strong>Spring / Summer 2025</strong></span>
    <span>✦ New Arrivals Weekly</span>
    <span>✦ <strong>Free Returns</strong> within 30 days</span>
    <span>✦ Members get <strong>15% Off</strong></span>
  </div>
</div>

<!-- ═══ FEATURED COLLECTIONS ═══ -->
<section class="section" id="featured">
  <div class="section-head">
    <div class="section-eyebrow">Browse by Category</div>
    <h2 class="section-title">Featured <em>Collections</em></h2>
    <p class="section-sub">From effortless everyday pieces to statement looks — there's a collection for every chapter of your story.</p>
  </div>

  <div class="collection-grid">

    <!-- Card 1 – Outerwear (large) -->
    <div class="col-card">
      <svg class="col-svg" viewBox="0 0 600 700" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="cg1" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#3d1000"/>
            <stop offset="100%" style="stop-color:#700818"/>
          </linearGradient>
        </defs>
        <rect width="600" height="700" fill="url(#cg1)"/>
        <path d="M150 120 Q300 80 450 120 L500 260 Q380 220 300 228 Q220 220 100 260 Z" fill="#c01030" opacity=".8"/>
        <path d="M100 260 Q220 220 300 228 Q380 220 500 260 L520 650 Q300 680 80 650 Z" fill="#a00820" opacity=".9"/>
        <path d="M100 260 L30 500 Q30 540 80 540 L130 540 L145 320 Z" fill="#a00820" opacity=".9"/>
        <path d="M500 260 L570 500 Q570 540 520 540 L470 540 L455 320 Z" fill="#a00820" opacity=".9"/>
        <!-- collar fur -->
        <path d="M200 140 Q300 110 400 140 Q380 165 300 170 Q220 165 200 140Z" fill="rgba(255,220,180,0.3)"/>
        <!-- belt -->
        <rect x="160" y="420" width="280" height="12" rx="4" fill="rgba(240,112,32,0.6)"/>
        <rect x="284" y="414" width="32" height="24" rx="2" fill="rgba(240,112,32,0.8)"/>
        <!-- buttons -->
        <circle cx="300" cy="310" r="6" fill="rgba(255,255,255,0.2)"/>
        <circle cx="300" cy="360" r="6" fill="rgba(255,255,255,0.2)"/>
        <circle cx="300" cy="410" r="6" fill="rgba(255,255,255,0.2)"/>
        <!-- skin -->
        <rect x="268" y="370" width="64" height="90" rx="14" fill="#e8b890" opacity=".9"/>
        <ellipse cx="300" cy="290" rx="80" ry="90" fill="#e8b890" opacity=".9"/>
        <!-- hair -->
        <path d="M220 240 Q220 180 300 175 Q380 180 380 240 Q365 210 300 212 Q235 210 220 240Z" fill="#2c1208"/>
      </svg>
      <div class="col-card-overlay"></div>
      <div class="col-card-content">
        <div class="col-tag">Outerwear</div>
        <div class="col-name">The<br>Coat Edit</div>
        <div class="col-count">48 Pieces</div>
        <a href="shop.html" class="col-cta">
          Shop Now
          <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
        </a>
      </div>
    </div>

    <!-- Card 2 – Dresses -->
    <div class="col-card">
      <svg class="col-svg" viewBox="0 0 400 300" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="cg2" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#f07020"/>
            <stop offset="100%" style="stop-color:#c04010"/>
          </linearGradient>
        </defs>
        <rect width="400" height="300" fill="url(#cg2)"/>
        <path d="M130 60 Q200 45 270 60 L295 120 Q240 105 200 108 Q160 105 105 120 Z" fill="rgba(255,255,255,0.15)"/>
        <path d="M105 120 Q160 105 200 108 Q240 105 295 120 L320 290 Q200 305 80 290 Z" fill="rgba(255,255,255,0.1)"/>
        <ellipse cx="200" cy="165" rx="80" ry="85" fill="rgba(255,220,180,0.5)" opacity=".6"/>
        <!-- floral pattern overlay -->
        <circle cx="150" cy="180" r="18" fill="rgba(192,16,48,0.25)"/>
        <circle cx="250" cy="200" r="14" fill="rgba(192,16,48,0.2)"/>
        <circle cx="190" cy="240" r="10" fill="rgba(192,16,48,0.15)"/>
      </svg>
      <div class="col-card-overlay"></div>
      <div class="col-card-content">
        <div class="col-tag">Dresses</div>
        <div class="col-name">Summer Dresses</div>
        <div class="col-count">32 Pieces</div>
        <a href="shop.html" class="col-cta">Shop Now <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg></a>
      </div>
    </div>

    <!-- Card 3 – Bags -->
    <div class="col-card">
      <svg class="col-svg" viewBox="0 0 300 300" xmlns="http://www.w3.org/2000/svg">
        <rect width="300" height="300" fill="#1a0a05"/>
        <rect x="55" y="110" width="190" height="160" rx="12" fill="#c01030"/>
        <path d="M95 110 Q95 60 150 60 Q205 60 205 110" fill="none" stroke="#e02040" stroke-width="7" stroke-linecap="round"/>
        <rect x="118" y="178" width="64" height="14" rx="4" fill="rgba(255,255,255,0.2)"/>
        <line x1="55" y1="155" x2="245" y2="155" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>
        <circle cx="150" cy="192" r="8" fill="rgba(240,112,32,0.7)"/>
        <!-- shadow -->
        <ellipse cx="150" cy="275" rx="90" ry="10" fill="rgba(0,0,0,0.3)"/>
      </svg>
      <div class="col-card-overlay"></div>
      <div class="col-card-content">
        <div class="col-tag">Bags</div>
        <div class="col-name">Bag Collection</div>
        <div class="col-count">24 Pieces</div>
        <a href="shop.html" class="col-cta">Shop Now <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg></a>
      </div>
    </div>

    <!-- Card 4 – Knitwear -->
    <div class="col-card">
      <svg class="col-svg" viewBox="0 0 380 300" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="cg4" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" style="stop-color:#fde8d8"/>
            <stop offset="100%" style="stop-color:#f5c8a0"/>
          </linearGradient>
        </defs>
        <rect width="380" height="300" fill="url(#cg4)"/>
        <!-- cable knit texture lines -->
        <path d="M60 60 Q90 75 120 60 Q150 45 180 60 Q210 75 240 60 Q270 45 300 60 Q330 75 360 60" fill="none" stroke="rgba(192,100,50,0.25)" stroke-width="3"/>
        <path d="M60 80 Q90 95 120 80 Q150 65 180 80 Q210 95 240 80 Q270 65 300 80 Q330 95 360 80" fill="none" stroke="rgba(192,100,50,0.2)" stroke-width="3"/>
        <path d="M60 100 Q90 115 120 100 Q150 85 180 100 Q210 115 240 100 Q270 85 300 100 Q330 115 360 100" fill="none" stroke="rgba(192,100,50,0.18)" stroke-width="3"/>
        <!-- sweater shape -->
        <path d="M90 80 Q190 60 290 80 L315 145 L270 132 L268 275 Q190 285 112 275 L110 132 L65 145 Z" fill="rgba(240,112,32,0.75)"/>
        <path d="M90 80 L65 145 L25 250 Q24 268 60 268 L90 268 L110 132 Z" fill="rgba(208,80,16,0.7)"/>
        <path d="M290 80 L315 145 L355 250 Q356 268 320 268 L290 268 L268 132 Z" fill="rgba(208,80,16,0.7)"/>
      </svg>
      <div class="col-card-overlay"></div>
      <div class="col-card-content">
        <div class="col-tag">Knitwear</div>
        <div class="col-name">Cosy Knits</div>
        <div class="col-count">19 Pieces</div>
        <a href="shop.html" class="col-cta">Shop Now <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg></a>
      </div>
    </div>

    <!-- Card 5 – Shoes -->
    <div class="col-card">
      <svg class="col-svg" viewBox="0 0 380 300" xmlns="http://www.w3.org/2000/svg">
        <rect width="380" height="300" fill="#3d0010"/>
        <path d="M40 195 Q65 130 130 120 Q180 114 215 128 L270 175 Q248 183 225 179 Q175 172 70 200 Z" fill="#c01030"/>
        <rect x="40" y="195" width="230" height="26" rx="6" fill="#8a0820"/>
        <!-- heel -->
        <rect x="30" y="185" width="20" height="36" rx="3" fill="#8a0820"/>
        <!-- second shoe -->
        <path d="M200 225 Q225 165 275 158 Q315 153 345 168 L370 210 Q355 216 338 213 Q305 208 210 230 Z" fill="#e02040" opacity=".6"/>
        <rect x="200" y="225" width="170" height="20" rx="4" fill="#700818" opacity=".7"/>
        <!-- shine -->
        <path d="M80 135 Q100 125 130 130" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <div class="col-card-overlay"></div>
      <div class="col-card-content">
        <div class="col-tag">Footwear</div>
        <div class="col-name">Shoe Edit</div>
        <div class="col-count">27 Pieces</div>
        <a href="shop.html" class="col-cta">Shop Now <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg></a>
      </div>
    </div>

  </div>
</section>

<!-- ═══ SEASON TABS ═══ -->
<section class="season-section">
  <div class="section-head">
    <div class="section-eyebrow">Curated Edits</div>
    <h2 class="section-title">Shop by <em>Season</em></h2>
  </div>

  <div class="season-tabs">
    <button class="season-tab active" onclick="switchSeason(this,'ss25')">SS 2025</button>
    <button class="season-tab" onclick="switchSeason(this,'fw24')">FW 2024</button>
    <button class="season-tab" onclick="switchSeason(this,'resort')">Resort</button>
    <button class="season-tab" onclick="switchSeason(this,'archive')">Archive</button>
  </div>

  <div class="season-content active" id="ss25">
    <div class="season-products" id="seasonGrid"></div>
  </div>
  <div class="season-content" id="fw24">
    <div class="season-products" id="fw24Grid"></div>
  </div>
  <div class="season-content" id="resort">
    <div class="season-products" id="resortGrid"></div>
  </div>
  <div class="season-content" id="archive">
    <div class="season-products" id="archiveGrid"></div>
  </div>
</section>

<!-- ═══ DESIGNER SPOTLIGHT ═══ -->
<section class="spotlight-section">
  <div class="spotlight-grid">
    <div class="spotlight-left">
      <div class="spotlight-eyebrow">Designer Spotlight</div>
      <h2 class="spotlight-title">Crafted with<br><em>intention</em></h2>
      <p class="spotlight-body">Each Supro collection begins with a story — a mood, a place, a feeling. Our designers translate that vision into pieces that balance the timeless with the contemporary, never chasing trends but always arriving ahead of them.</p>
      <div class="spotlight-stats">
        <div class="spot-stat"><span class="num">240+</span><span class="lbl">Products</span></div>
        <div class="spot-stat"><span class="num">18</span><span class="lbl">Designers</span></div>
        <div class="spot-stat"><span class="num">4.8★</span><span class="lbl">Avg Rating</span></div>
      </div>
      <a href="shop.html" class="btn-primary" style="align-self:flex-start">Explore All</a>
    </div>
    <div class="spotlight-right">
      <!-- tall left image -->
      <div class="spot-img">
        <svg viewBox="0 0 280 520" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
          <defs>
            <linearGradient id="sg1" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" style="stop-color:#3d1000"/>
              <stop offset="100%" style="stop-color:#700818"/>
            </linearGradient>
          </defs>
          <rect width="280" height="520" fill="url(#sg1)"/>
          <path d="M70 160 Q140 140 210 160 L230 230 Q175 215 140 218 Q105 215 50 230 Z" fill="#c01030" opacity=".8"/>
          <path d="M50 230 Q105 215 140 218 Q175 215 230 230 L240 490 Q140 505 40 490 Z" fill="#a00820" opacity=".9"/>
          <path d="M50 230 L15 400 Q15 430 45 430 L70 430 L80 270 Z" fill="#a00820" opacity=".9"/>
          <path d="M230 230 L265 400 Q265 430 235 430 L210 430 L200 270 Z" fill="#a00820" opacity=".9"/>
          <rect x="100" y="325" width="80" height="10" rx="3" fill="rgba(240,112,32,0.5)"/>
          <rect x="126" y="318" width="28" height="22" rx="2" fill="rgba(240,112,32,0.7)"/>
          <rect x="108" y="140" width="64" height="70" rx="14" fill="#e8b890"/>
          <ellipse cx="140" cy="100" rx="62" ry="72" fill="#e8b890"/>
          <path d="M78 68 Q80 28 140 22 Q200 28 202 68 Q192 44 140 46 Q88 44 78 68Z" fill="#2c1208"/>
        </svg>
        <div class="spot-img-label">The Crimson Coat</div>
      </div>
      <!-- two smaller -->
      <div class="spot-img">
        <svg viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
          <rect width="240" height="240" fill="#fff0e8"/>
          <path d="M55 50 Q120 35 185 50 L205 105 Q165 90 120 93 Q75 90 35 105 Z" fill="#f07020"/>
          <path d="M35 105 Q75 90 120 93 Q165 90 205 105 L215 230 Q120 242 25 230 Z" fill="#d05010"/>
          <path d="M35 105 L8 195 Q8 212 28 212 L48 212 L55 135 Z" fill="#d05010"/>
          <path d="M205 105 L232 195 Q232 212 212 212 L192 212 L185 135 Z" fill="#d05010"/>
        </svg>
        <div class="spot-img-label">Linen Blazer</div>
      </div>
      <div class="spot-img">
        <svg viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
          <rect width="240" height="240" fill="#3d0010"/>
          <path d="M35 155 Q50 105 100 98 Q135 94 160 108 L195 145 Q178 152 162 148 Q128 142 45 162 Z" fill="#c01030"/>
          <rect x="35" y="155" width="162" height="20" rx="4" fill="#8a0820"/>
          <rect x="28" y="148" width="16" height="28" rx="3" fill="#8a0820"/>
          <path d="M65 108 Q80 100 105 104" fill="none" stroke="rgba(255,255,255,0.18)" stroke-width="3" stroke-linecap="round"/>
        </svg>
        <div class="spot-img-label">Statement Heels</div>
      </div>
    </div>
  </div>
</section>

<!-- ═══ LOOKBOOK BANNER ═══ -->
<div class="lookbook-banner">
  <div class="lookbook-text">
    <div class="lookbook-tag">SS 2025 Lookbook</div>
    <h2 class="lookbook-title">Style is a<br>way to say who<br>you are.</h2>
    <p class="lookbook-sub">Browse our full editorial lookbook — each image tells a story, each piece is available to shop directly.</p>
  </div>
  <div class="lookbook-actions">
    <a href="#" class="btn-white" onclick="showToast('Lookbook opening soon…'); return false;">View Lookbook</a>
    <a href="shop.html" class="btn-outline-white">Shop The Look</a>
  </div>
</div>

<!-- ═══ TRENDING NOW ═══ -->
<section class="trends-section">
  <div class="section-head">
    <div class="section-eyebrow">What's Hot</div>
    <h2 class="section-title">Trending <em>Now</em></h2>
  </div>
  <div class="trends-row">
    <div class="trend-card" onclick="showToast('Browsing Workwear Edit…')">
      <div class="trend-bg">
        <svg viewBox="0 0 400 380" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
          <defs>
            <linearGradient id="tg1" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" style="stop-color:#1a0a05"/>
              <stop offset="100%" style="stop-color:#3d1000"/>
            </linearGradient>
          </defs>
          <rect width="400" height="380" fill="url(#tg1)"/>
          <path d="M90 90 Q200 60 310 90 L340 180 Q255 150 200 154 Q145 150 60 180 Z" fill="#8a0820" opacity=".7"/>
          <path d="M60 180 Q145 150 200 154 Q255 150 340 180 L355 360 Q200 375 45 360 Z" fill="#700818" opacity=".85"/>
          <path d="M60 180 L20 310 Q20 340 50 340 L80 340 L90 220 Z" fill="#700818" opacity=".85"/>
          <path d="M340 180 L380 310 Q380 340 350 340 L320 340 L310 220 Z" fill="#700818" opacity=".85"/>
          <rect x="165" y="255" width="70" height="10" rx="3" fill="rgba(240,112,32,0.4)"/>
          <rect x="185" y="248" width="30" height="22" rx="2" fill="rgba(240,112,32,0.55)"/>
          <rect x="168" y="72" width="64" height="72" rx="14" fill="#e8b890" opacity=".9"/>
          <ellipse cx="200" cy="44" rx="58" ry="66" fill="#e8b890" opacity=".9"/>
          <path d="M142 18 Q144 -14 200 -20 Q256 -14 258 18 Q246 -2 200 0 Q154 -2 142 18Z" fill="#2c1208"/>
        </svg>
      </div>
      <div class="trend-overlay"></div>
      <div class="trend-content">
        <span class="trend-num">01</span>
        <div class="trend-name">The Workwear Edit</div>
        <div class="trend-count">34 pieces · Updated weekly</div>
      </div>
      <div class="trend-arrow">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
      </div>
    </div>

    <div class="trend-card" onclick="showToast('Browsing Weekend Casual…')">
      <div class="trend-bg">
        <svg viewBox="0 0 400 380" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
          <rect width="400" height="380" fill="#fff0e8"/>
          <path d="M80 80 Q200 55 320 80 L348 160 Q262 135 200 138 Q138 135 52 160 Z" fill="#f07020" opacity=".85"/>
          <path d="M52 160 Q138 135 200 138 Q262 135 348 160 L360 360 Q200 375 40 360 Z" fill="#d05010" opacity=".9"/>
          <path d="M52 160 L12 290 Q12 320 44 320 L76 320 L88 200 Z" fill="#d05010" opacity=".9"/>
          <path d="M348 160 L388 290 Q388 320 356 320 L324 320 L312 200 Z" fill="#d05010" opacity=".9"/>
          <!-- jeans pocket detail -->
          <path d="M150 260 Q170 250 190 260" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="2"/>
          <ellipse cx="200" cy="96" rx="68" ry="80" fill="#f0c8a0" opacity=".9"/>
          <path d="M132 52 Q134 12 200 6 Q266 12 268 52 Q256 28 200 30 Q144 28 132 52Z" fill="#3d2010"/>
        </svg>
      </div>
      <div class="trend-overlay"></div>
      <div class="trend-content">
        <span class="trend-num">02</span>
        <div class="trend-name">Weekend Casual</div>
        <div class="trend-count">28 pieces · Fresh drop</div>
      </div>
      <div class="trend-arrow">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
      </div>
    </div>

    <div class="trend-card" onclick="showToast('Browsing Evening Wear…')">
      <div class="trend-bg">
        <svg viewBox="0 0 400 380" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%">
          <rect width="400" height="380" fill="#0a0208"/>
          <path d="M140 60 Q200 40 260 60 L280 120 Q230 105 200 108 Q170 105 120 120 Z" fill="#c01030" opacity=".7"/>
          <path d="M120 120 Q170 105 200 108 Q230 105 280 120 L310 370 Q200 385 90 370 Z" fill="#8a0820" opacity=".9"/>
          <!-- velvet sheen -->
          <path d="M150 150 Q200 140 250 150 Q240 170 200 175 Q160 170 150 150Z" fill="rgba(255,255,255,0.04)"/>
          <path d="M140 200 Q200 190 260 200" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>
          <path d="M130 240 Q200 230 270 240" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="1"/>
          <path d="M120 280 Q200 270 280 280" fill="none" stroke="rgba(255,255,255,0.04)" stroke-width="1"/>
          <rect x="168" y="40" width="64" height="64" rx="14" fill="#e8b890" opacity=".9"/>
          <ellipse cx="200" cy="18" rx="52" ry="60" fill="#e8b890" opacity=".9"/>
          <path d="M148 -6 Q150 -38 200 -44 Q250 -38 252 -6 Q240 -26 200 -24 Q160 -26 148 -6Z" fill="#1a0810"/>
          <!-- sparkle -->
          <circle cx="240" cy="200" r="2" fill="rgba(240,112,32,0.6)"/>
          <circle cx="160" cy="240" r="1.5" fill="rgba(240,112,32,0.5)"/>
          <circle cx="220" cy="280" r="1" fill="rgba(240,112,32,0.4)"/>
        </svg>
      </div>
      <div class="trend-overlay"></div>
      <div class="trend-content">
        <span class="trend-num">03</span>
        <div class="trend-name">Evening Wear</div>
        <div class="trend-count">21 pieces · Exclusive</div>
      </div>
      <div class="trend-arrow">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
      </div>
    </div>
  </div>
</section>

<!-- ═══ NEWSLETTER ═══ -->
<section class="newsletter">
  <div class="newsletter-inner">
    <div class="newsletter-eyebrow">Stay in the Loop</div>
    <h2 class="newsletter-title">Join the Supro Community</h2>
    <p class="newsletter-sub">Be first to know about new collections, exclusive drops, and members-only offers. Plus 15% off your first order.</p>
    <div class="newsletter-form">
      <input class="newsletter-input" type="email" id="emailInput" placeholder="Your email address" />
      <button class="newsletter-btn" onclick="handleNewsletter()">Subscribe</button>
    </div>
  </div>
</section>

<!-- ═══ FOOTER ═══ -->
<footer>
  <div class="footer-brand">Supro<span>.</span></div>
  <div class="footer-tagline">Contemporary Fashion · Est. 2020</div>
  <div class="footer-grid">
    <div class="footer-col">
      <h4>About</h4>
      <ul>
        <li><a href="#">About Us</a></li>
        <li><a href="#">Careers</a></li>
        <li><a href="#">Corporate</a></li>
        <li><a href="#">Sustainability</a></li>
      </ul>
    </div>
    <div class="footer-col"><h4>Help</h4><ul><li><a href="#">Track Order</a></li><li><a href="#">Delivery & Returns</a></li><li><a href="#">FAQs</a></li></ul></div>
    <div class="footer-col"><h4>Shop</h4><ul><li><a href="shop.html">All Products</a></li><li><a href="#">New Arrivals</a></li><li><a href="#">Sale</a></li></ul></div>
    <div class="footer-col"><h4>Language</h4><ul><li><select class="footer-select"><option>English</option><option>Français</option><option>Deutsch</option></select></li></ul></div>
    <div class="footer-col"><h4>Currency</h4><ul><li><select class="footer-select"><option>GBP</option><option>USD</option><option>EUR</option><option>BDT</option></select></li></ul></div>
  </div>
  <div class="footer-bottom">
    <p>© 2025 <strong style="color:rgba(255,255,255,0.5)">Supro</strong>. All rights reserved.</p>
    <div>
      <a href="#">Privacy Policy</a><a href="#">Terms of Use</a>
      <button class="back-top" onclick="window.scrollTo({top:0,behavior:'smooth'})">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 15l-6-6-6 6"/></svg>
      </button>
    </div>
  </div>
</footer>

<script>
// ─── PRODUCT SVGs ──────────────────────────────────────────────────────────────
const svgs = {
  dress1:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff0e8"/><path d="M60 40 Q90 30 120 40 L140 80 Q110 70 90 72 Q70 70 40 80 Z" fill="#c01030"/><path d="M40 80 Q70 70 90 72 Q110 70 140 80 L155 200 Q90 210 25 200 Z" fill="#a00820" opacity=".9"/></svg>`,
  jacket1:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff5ee"/><path d="M50 50 Q90 40 130 50 L150 90 Q120 80 90 82 Q60 80 30 90 Z" fill="#f07020"/><path d="M30 90 Q60 80 90 82 Q120 80 150 90 L160 195 Q90 205 20 195 Z" fill="#d05010"/><path d="M30 90 L10 170 Q10 185 25 185 L40 185 L45 120 Z" fill="#d05010"/><path d="M150 90 L170 170 Q170 185 155 185 L140 185 L135 120 Z" fill="#d05010"/></svg>`,
  parka:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#f8f8f8"/><path d="M55 40 Q90 28 125 40 L140 80 Q110 68 90 70 Q70 68 40 80 Z" fill="#1a0a05"/><path d="M40 80 Q70 68 90 70 Q110 68 140 80 L148 195 Q90 205 32 195 Z" fill="#1a0a05"/><path d="M40 80 L15 165 Q15 182 32 182 L45 182 L50 115 Z" fill="#1a0a05"/><path d="M140 80 L165 165 Q165 182 148 182 L135 182 L130 115 Z" fill="#1a0a05"/></svg>`,
  dress2:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff0f0"/><path d="M62 40 Q90 32 118 40 L125 80 Q90 74 55 80 Z" fill="#f07020"/><path d="M55 80 Q90 74 125 80 L142 200 Q90 210 38 200 Z" fill="#c85810"/></svg>`,
  tshirt:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff8f8"/><path d="M55 50 Q90 38 125 50 L142 95 L120 88 L118 185 Q90 192 62 185 L60 88 L38 95 Z" fill="#f07020"/></svg>`,
  bag1:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff5ee"/><path d="M60 90 Q60 65 90 65 Q120 65 120 90" fill="none" stroke="#c01030" stroke-width="4"/><rect x="45" y="90" width="90" height="80" rx="6" fill="#c01030"/></svg>`,
  bag2:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff5ee"/><ellipse cx="90" cy="130" rx="55" ry="62" fill="#f07020"/><path d="M60 90 Q90 60 120 90" fill="none" stroke="#c04810" stroke-width="5" stroke-linecap="round"/></svg>`,
  shoes:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fdf0e8"/><path d="M25 140 Q35 90 75 85 Q105 82 125 90 L155 125 Q140 130 130 128 Q100 125 40 145 Z" fill="#c01030"/><rect x="25" y="140" width="130" height="16" rx="4" fill="#8a0820"/></svg>`,
  sweater:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff5ee"/><path d="M52 48 Q90 38 128 48 L142 92 L120 86 L118 190 Q90 198 62 190 L60 86 L38 92 Z" fill="#f07020"/><path d="M52 48 L38 92 L15 175 Q14 188 32 188 L44 188 L60 86 Z" fill="#d05010"/><path d="M128 48 L142 92 L165 175 Q166 188 148 188 L136 188 L120 86 Z" fill="#d05010"/></svg>`,
  backpack:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff0e8"/><rect x="42" y="55" width="96" height="130" rx="14" fill="#c01030"/><rect x="65" y="40" width="50" height="28" rx="10" fill="none" stroke="#c01030" stroke-width="4"/><rect x="55" y="95" width="70" height="50" rx="6" fill="#e02040"/></svg>`,
  denim:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fdf5f0"/><path d="M55 40 Q90 30 125 40 L135 85 Q90 78 45 85 Z" fill="#c01030"/><path d="M45 85 Q90 78 135 85 L140 195 Q90 205 40 195 Z" fill="#a00820"/><line x1="90" y1="85" x2="90" y2="195" stroke="#880618" stroke-width="2"/></svg>`,
  glasses:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#f8f8f8"/><ellipse cx="65" cy="110" rx="38" ry="26" fill="rgba(240,112,32,0.1)" stroke="#f07020" stroke-width="3"/><ellipse cx="115" cy="110" rx="38" ry="26" fill="rgba(240,112,32,0.1)" stroke="#f07020" stroke-width="3"/><line x1="103" y1="110" x2="77" y2="110" stroke="#f07020" stroke-width="3"/></svg>`,
  jacket2:`<svg viewBox="0 0 180 220" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="220" fill="#fff0e8"/><path d="M52 45 Q90 35 128 45 L142 88 Q112 76 90 78 Q68 76 38 88 Z" fill="#e06018"/><path d="M38 88 Q68 76 90 78 Q112 76 142 88 L150 195 Q90 205 30 195 Z" fill="#c85010"/><path d="M38 88 L12 168 Q12 184 30 184 L44 184 L48 118 Z" fill="#c85010"/><path d="M142 88 L168 168 Q168 184 150 184 L136 184 L132 118 Z" fill="#c85010"/></svg>`,
};

// Season product data
const seasonData = {
  ss25: [
    {id:1, name:"Floral Short Jumpsuit",    price:"£97.99",  priceval:97.99,  stars:4, badge:"hot",  img:"dress1"},
    {id:2, name:"Embroidered Flowy Jacket", price:"£56.89",  priceval:56.89,  stars:4, badge:null,   img:"jacket1"},
    {id:3, name:"Wrap Back Dress",          price:"£59.90",  priceval:59.90,  oldPrice:"£77.98", stars:4, badge:"sale", img:"dress2"},
    {id:4, name:"Contrasting T-Shirt",      price:"£95.90",  priceval:95.90,  stars:5, badge:"hot",  img:"tshirt"},
    {id:5, name:"Leather Shop Bag",         price:"£59.90",  priceval:59.90,  stars:5, badge:null,   img:"bag2"},
    {id:6, name:"Metallic Sunglasses",      price:"£34.90",  priceval:34.90,  stars:4, badge:"new",  img:"glasses"},
    {id:7, name:"Cropped Denim Jumpsuit",   price:"£80.59",  priceval:80.59,  stars:4, badge:null,   img:"denim"},
    {id:8, name:"Glitter Mule",             price:"£56.90",  priceval:56.90,  stars:3, badge:null,   img:"shoes"},
  ],
  fw24: [
    {id:9,  name:"Furry Hooded Parka",      price:"£77.98",  priceval:77.98,  stars:5, badge:null,   img:"parka"},
    {id:10, name:"Shearling Jacket",        price:"£179.90", priceval:179.90, stars:5, badge:null,   img:"jacket2"},
    {id:11, name:"Oversize Sweater",        price:"£59.90",  priceval:59.90,  oldPrice:"£77.90", stars:5, badge:"sale", img:"sweater"},
    {id:12, name:"Contrast Backpack",       price:"£69.90",  priceval:69.90,  stars:4, badge:null,   img:"backpack"},
  ],
  resort: [
    {id:13, name:"Mango Women's Bag",       price:"£79.90",  priceval:79.90,  oldPrice:"£89.90", stars:4, badge:"sale", img:"bag1"},
    {id:14, name:"Silk Midi Skirt",         price:"£64.90",  priceval:64.90,  stars:5, badge:"hot",  img:"dress1"},
    {id:15, name:"Linen Blazer",            price:"£89.90",  priceval:89.90,  stars:4, badge:"new",  img:"jacket1"},
    {id:16, name:"Platform Loafers",        price:"£95.90",  priceval:95.90,  stars:5, badge:null,   img:"shoes"},
  ],
  archive: [
    {id:17, name:"Classic Trench Coat",     price:"£145.90", priceval:145.90, stars:5, badge:null,   img:"parka"},
    {id:18, name:"Velvet Mini Dress",       price:"£79.90",  priceval:79.90,  stars:5, badge:"hot",  img:"dress2"},
    {id:19, name:"Chunky Knit Cardigan",    price:"£85.90",  priceval:85.90,  stars:4, badge:null,   img:"sweater"},
    {id:20, name:"Leather Crossbody Bag",   price:"£69.90",  priceval:69.90,  oldPrice:"£89.90", stars:4, badge:"sale", img:"bag1"},
  ],
};

let cart = [], wishlist = [];
let searchOpen=false, accountOpen=false, cartOpen=false, wishOpen=false, drawerOpen=false;

// ─── UTILITIES ─────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2600);
}
function setOverlay(on){ document.getElementById('overlay').classList.toggle('active',on); }
function closeAll(except) {
  if(except!=='search'&&searchOpen) toggleSearch();
  if(except!=='account'&&accountOpen){
    accountOpen=false;
    document.getElementById('accountDropdown').classList.remove('open');
    document.getElementById('accountBtn').classList.remove('active-btn');
  }
  if(except!=='cart'&&cartOpen) closeCart();
  if(except!=='wish'&&wishOpen) closeWishlist();
  if(except!=='drawer'&&drawerOpen) closeMobileDrawer();
}

// ─── MOBILE DRAWER ─────────────────────────────────────────────────────────────
function toggleMobileDrawer(){ drawerOpen?closeMobileDrawer():openMobileDrawer(); }
function openMobileDrawer(){
  closeAll('drawer'); drawerOpen=true;
  document.getElementById('mobileDrawer').classList.add('open');
  document.getElementById('drawerScrim').classList.add('active');
  document.getElementById('hamburgerBtn').classList.add('open');
}
function closeMobileDrawer(){
  drawerOpen=false;
  document.getElementById('mobileDrawer').classList.remove('open');
  document.getElementById('drawerScrim').classList.remove('active');
  document.getElementById('hamburgerBtn').classList.remove('open');
}
function setDrawerActive(el){
  document.querySelectorAll('.drawer-nav-item').forEach(i=>i.classList.remove('active'));
  el.classList.add('active');
}

// ─── SEARCH ────────────────────────────────────────────────────────────────────
function toggleSearch(){
  searchOpen=!searchOpen;
  document.getElementById('searchBar').classList.toggle('open',searchOpen);
  document.getElementById('searchBtn').classList.toggle('active-btn',searchOpen);
  if(searchOpen){ closeAll('search'); setTimeout(()=>document.getElementById('searchInput').focus(),300); }
  else setOverlay(cartOpen||wishOpen);
}
function handleSearchKey(e){ if(e.key==='Enter') doSearch(); if(e.key==='Escape') toggleSearch(); }
function doSearch(){
  const q=document.getElementById('searchInput').value.trim();
  if(q){ showToast(`Searching for "${q}"…`); document.getElementById('searchInput').value=''; toggleSearch(); }
}

// ─── ACCOUNT ───────────────────────────────────────────────────────────────────
function toggleAccount(e){
  e&&e.stopPropagation(); accountOpen=!accountOpen;
  if(accountOpen){
    closeAll('account'); accountOpen=true;
    const btn=document.getElementById('accountBtn'), rect=btn.getBoundingClientRect(), dd=document.getElementById('accountDropdown');
    dd.style.top=(rect.bottom+6)+'px'; dd.style.right=(window.innerWidth-rect.right)+'px'; dd.style.left='auto';
  }
  document.getElementById('accountDropdown').classList.toggle('open',accountOpen);
  document.getElementById('accountBtn').classList.toggle('active-btn',accountOpen);
}
document.addEventListener('click',e=>{
  if(accountOpen&&!e.target.closest('#accountDropdown')&&!e.target.closest('#accountBtn')){
    accountOpen=false;
    document.getElementById('accountDropdown').classList.remove('open');
    document.getElementById('accountBtn').classList.remove('active-btn');
  }
});

// ─── CART ──────────────────────────────────────────────────────────────────────
function updateCartCount(){
  const total=cart.reduce((s,i)=>s+i.qty,0);
  const el=document.getElementById('cartCount');
  el.textContent=total; el.classList.add('bump');
  setTimeout(()=>el.classList.remove('bump'),300);
}
function addToCart(product){
  const ex=cart.find(i=>i.id===product.id);
  if(ex) ex.qty++; else cart.push({...product,qty:1});
  updateCartCount(); showToast(`"${product.name}" added to cart`);
}
function openCart(){
  closeAll('cart'); cartOpen=true; renderCart();
  document.getElementById('cartPanel').classList.add('open');
  document.getElementById('cartBtn').classList.add('active-btn');
  setOverlay(true);
}
function closeCart(){
  cartOpen=false;
  document.getElementById('cartPanel').classList.remove('open');
  document.getElementById('cartBtn').classList.remove('active-btn');
  setOverlay(wishOpen);
}
function renderCart(){
  const body=document.getElementById('cartBody'), footer=document.getElementById('cartFooter');
  if(!cart.length){ body.innerHTML='<div class="empty-msg">Your cart is empty.<br>Start shopping to add items!</div>'; footer.innerHTML=''; return; }
  body.innerHTML=cart.map((item,i)=>`
    <div class="cart-item">
      <div class="cart-item-img">${svgs[item.img]||svgs.dress1}</div>
      <div class="cart-item-info">
        <div class="cart-item-name">${item.name}</div>
        <div class="cart-item-meta">Size: M &nbsp;|&nbsp; Color: Default</div>
        <div class="cart-item-row">
          <div class="qty-ctrl">
            <button onclick="changeQty(${i},-1)">−</button>
            <span>${item.qty}</span>
            <button onclick="changeQty(${i},1)">+</button>
          </div>
          <div class="cart-item-price">${item.price}</div>
        </div>
        <button class="remove-btn" onclick="removeFromCart(${i})">Remove</button>
      </div>
    </div>`).join('');
  const total=cart.reduce((s,i)=>s+i.priceval*i.qty,0);
  footer.innerHTML=`
    <div class="cart-total"><span>Subtotal</span><strong style="color:var(--crimson);font-size:1rem">£${total.toFixed(2)}</strong></div>
    <button class="checkout-btn" onclick="showToast('Redirecting to checkout…')">Proceed to Checkout</button>
    <button class="continue-btn" onclick="closeCart()">Continue Shopping</button>`;
}
function changeQty(idx,delta){ cart[idx].qty+=delta; if(cart[idx].qty<=0) cart.splice(idx,1); updateCartCount(); renderCart(); }
function removeFromCart(idx){ const n=cart[idx].name; cart.splice(idx,1); updateCartCount(); renderCart(); showToast(`"${n}" removed`); }

// ─── WISHLIST ──────────────────────────────────────────────────────────────────
function updateWishBadge(){ const el=document.getElementById('mobileWishBadge'); if(el) el.textContent=wishlist.length; }
function toggleWish(product,btn){
  const idx=wishlist.findIndex(i=>i.id===product.id);
  if(idx>-1){ wishlist.splice(idx,1); btn.classList.remove('wished'); showToast('Removed from wishlist'); }
  else { wishlist.push({...product}); btn.classList.add('wished'); showToast(`"${product.name}" wishlisted`); }
  btn.querySelector('svg').setAttribute('fill',btn.classList.contains('wished')?'currentColor':'none');
  updateWishBadge();
}
function openWishlist(){
  closeAll('wish'); wishOpen=true; renderWishlist();
  document.getElementById('wishlistPanel').classList.add('open');
  document.getElementById('wishlistBtn').classList.add('active-btn');
  setOverlay(true);
}
function closeWishlist(){
  wishOpen=false;
  document.getElementById('wishlistPanel').classList.remove('open');
  document.getElementById('wishlistBtn').classList.remove('active-btn');
  setOverlay(cartOpen);
}
function renderWishlist(){
  const body=document.getElementById('wishBody');
  if(!wishlist.length){ body.innerHTML='<div class="empty-msg">Your wishlist is empty.<br>Heart items to save them here.</div>'; return; }
  body.innerHTML=wishlist.map((item,i)=>`
    <div class="wish-item">
      <div style="width:62px;height:78px;background:var(--sand);flex-shrink:0;display:flex;align-items:center;justify-content:center">${svgs[item.img]||svgs.dress1}</div>
      <div style="flex:1">
        <div class="wish-item-name">${item.name}</div>
        <div class="wish-item-price">${item.price}</div>
        <button class="wish-add-btn" onclick="addToCart(wishlist[${i}])">Add to Cart</button>
      </div>
      <button style="background:none;border:none;cursor:pointer;color:#9a8075;align-self:flex-start;padding:4px" onclick="removeWish(${i})">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>`).join('');
}
function removeWish(idx){ wishlist.splice(idx,1); renderWishlist(); updateWishBadge(); }

// ─── PRODUCT CARDS ─────────────────────────────────────────────────────────────
function renderStars(n){ return Array.from({length:5},(_,i)=>`<span class="prod-star${i>=n?' empty':''}">★</span>`).join(''); }

function renderSeasonProducts(key, gridId){
  const grid=document.getElementById(gridId);
  if(!grid) return;
  grid.innerHTML='';
  seasonData[key].forEach((p,i)=>{
    const isWished=wishlist.some(w=>w.id===p.id);
    const card=document.createElement('div');
    card.className='prod-card';
    card.style.animationDelay=(i*0.06)+'s';
    card.innerHTML=`
      <div class="prod-img">
        ${p.badge?`<span class="prod-badge badge-${p.badge}">${p.badge}</span>`:''}
        ${svgs[p.img]||svgs.dress1}
        <button class="prod-wish${isWished?' wished':''}" data-id="${p.id}">
          <svg width="14" height="14" fill="${isWished?'currentColor':'none'}" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
        </button>
        <button class="prod-quick" data-id="${p.id}">Quick Add</button>
      </div>
      <div class="prod-name">${p.name}</div>
      <div class="prod-stars">${renderStars(p.stars)}</div>
      <div class="prod-price">${p.oldPrice?`<span class="old">${p.oldPrice}</span>`:''}<span class="cur">${p.price}</span></div>`;
    card.querySelector('.prod-wish').addEventListener('click',e=>{ e.stopPropagation(); toggleWish(p,card.querySelector('.prod-wish')); });
    card.querySelector('.prod-quick').addEventListener('click',e=>{ e.stopPropagation(); addToCart(p); });
    card.querySelector('.prod-img').addEventListener('click',e=>{ if(!e.target.closest('.prod-wish')&&!e.target.closest('.prod-quick')) showToast(`Viewing "${p.name}"`); });
    grid.appendChild(card);
  });
}

function switchSeason(btn, key){
  document.querySelectorAll('.season-tab').forEach(t=>t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.season-content').forEach(c=>c.classList.remove('active'));
  document.getElementById(key).classList.add('active');
  const gridMap = { ss25:'seasonGrid', fw24:'fw24Grid', resort:'resortGrid', archive:'archiveGrid' };
  renderSeasonProducts(key, gridMap[key]);
}

// ─── NEWSLETTER ────────────────────────────────────────────────────────────────
function handleNewsletter(){
  const v=document.getElementById('emailInput').value.trim();
  if(!v||!v.includes('@')){ showToast('Please enter a valid email address'); return; }
  showToast(`🎉 Welcome! 15% off code sent to ${v}`);
  document.getElementById('emailInput').value='';
}

// ─── INIT ──────────────────────────────────────────────────────────────────────
renderSeasonProducts('ss25','seasonGrid');
</script>
</body>
</html>

=================================================================
Demanded

convert to nuxt.js code with part by part distibute with file componets etc and must ad style every sigle raw code convert to nuxt.js code and 100% 

======================================================================



'''


system_p = '''

# SYSTEM PROMPT: HTML to Complete Nuxt.js Project Converter

## ROLE
You are an expert Nuxt.js 3 developer specializing in converting static HTML/CSS/JS into production-ready, fully functional Nuxt.js applications. You never produce incomplete code or placeholder comments.

## CORE RULES

### 1. ANALYZE HTML STRUCTURE
Scan the HTML and automatically identify:
- **Repeated elements** (cards, list items, rows) → Make components
- **Layout sections** (header, footer, sidebar) → Layout components  
- **Independent UI blocks** (modals, panels, dropdowns) → Reusable components
- **Static assets** (SVGs, images, icons) → Asset folder
- **Interactive elements** (buttons, forms, toggles) → Vue events

### 2. AUTOMATIC COMPONENT DISTRIBUTION

#### ALWAYS CREATE THESE FILES:

## OUTPUT STRUCTURE - Generate ALL these files:

### Complete File Tree:
### 3. COMPONENT EXTRACTION RULES

| If you see this pattern | Create this component |
|------------------------|----------------------|
| `<nav>` or navigation wrapper | `AppHeader.vue` |
| `<footer>` or footer wrapper | `AppFooter.vue` |
| Same div/card repeated 2+ times | `CardComponent.vue` |
| Modal, drawer, panel, dropdown | Separate component + store for visibility |
| `<form>` with validation | `FormComponent.vue` |
| List/table with items | `ListItem.vue` |
| Any SVG or icon | Import as asset or icon component |

### 4. STATE MANAGEMENT PATTERNS

**Automatically detect and use Pinia for:**
- Cart/Wishlist data → Store with localStorage
- Modal visibility → UI store
- User authentication → Auth store
- Any array that changes → Reactive store

### 5. JAVASCRIPT CONVERSION

| Vanilla JS | Nuxt/Vue |
|------------|----------|
| `document.getElementById()` | `ref()` or template ref |
| `addEventListener` | `@click`, `@input` |
| `classList.toggle()` | `:class="{ active: isOpen }"` |
| `innerHTML` | `v-html` or reactive variable |
| `fetch()` | `useFetch()` or `$fetch` |
| `localStorage` | Pinia store with persistence |
| `setTimeout/setInterval` | Use lifecycle hooks |

### 6. CSS HANDLING

- **ALL styles** → `assets/css/main.css`
- Remove inline styles, move to CSS file
- Preserve all animations and keyframes
- Keep responsive breakpoints as-is
- Add `scoped` to component styles only if needed

### 7. OUTPUT STRUCTURE (MANDATORY)

Generate EVERY section below with complete code:

#### Part 1: Setup Files
- [ ] `package.json` with all dependencies
- [ ] `nuxt.config.ts` with modules config
- [ ] `tsconfig.json`
- [ ] `.env.example`

#### Part 2: Layout
- [ ] `layouts/default.vue` - Main layout with slots
- [ ] Extract header/footer from HTML

#### Part 3: Pages
- [ ] `pages/index.vue` - Main content
- [ ] Create additional pages based on `<a href="...">`

#### Part 4: Components (Create ALL that apply)
- [ ] All repeated UI elements as components
- [ ] Modal/drawer panels as components
- [ ] Form inputs as components

#### Part 5: Stores (Create if needed)
- [ ] `stores/mainStore.ts` - Global state
- [ ] Add Pinia persistence

#### Part 6: Composables (Create if needed)
- [ ] `composables/useToast.ts` - Notification system
- [ ] `composables/useModal.ts` - Modal management

#### Part 7: Assets
- [ ] `assets/css/main.css` - ALL CSS
- [ ] Extract images to `/public`

### 8. COMPONENT NAMING CONVENTION


project/
├── package.json
├── nuxt.config.ts
├── tsconfig.json
├── app.vue
├── .env.example
├── README.md
├── layouts/
│ └── default.vue
├── pages/
│ ├── index.vue
│ └── [all other pages from HTML].vue
├── components/
│ ├── Header.vue
│ ├── Footer.vue
│ └── [all reusable components].vue
├── composables/
│ ├── useApi.ts
│ └── [all custom composables].vue
├── stores/
│ ├── index.ts (Pinia store setup)
│ └── mainStore.ts
├── assets/
│ ├── css/
│ │ └── main.css (converted from HTML styles)
│ └── images/ (with image files)
├── public/
│ └── favicon.ico
├── utils/
│ └── helpers.ts
├── plugins/
│ ├── pinia.client.ts
│ └── [other plugins].ts
└── server/
├── api/
│ └── [api routes if needed].ts
└── middleware/
└── [server middleware].ts


'''


while True:
    print('start----------------')
    stream = ollama.chat(
        model='qwen2.5-coder:3b',
        messages=[
            {'role': 'system', 'content': system_p},
            {'role': 'user', 'content': prompt}
        ],
        stream=True,
        keep_alive=-1  
    )
    full_response = ""
    char_count = 0
    for chunk in stream:
        content = chunk['message']['content']
        print(content, end='', flush=True)
        full_response += content
        char_count += len(content)
    
    print("\n" + "─"*50)