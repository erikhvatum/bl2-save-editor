/* Dependency-free synthetic DOM smoke test. No browser, network or user files. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor(tag = 'div') { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.disabled = false; this.checked = false; }
    append(...children) { children.forEach(c => { c.parent = this; this.children.push(c); }); }
    appendChild(c) { this.append(c); }
    replaceChildren(...c) { this.children = []; this.append(...c); }
    addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); }
    setAttribute() {}
    get previousSibling() { return this.parent.children[this.parent.children.indexOf(this) - 1]; }
    checkValidity() { const n = Number(this.value); return Number.isFinite(n) && n >= this.min && n <= this.max; }
    reportValidity() { return this.checkValidity(); }
    async fire(event) { for (const fn of this.listeners[event] || []) await fn.call(this); }
    click() { return this.fire('click'); }
    querySelectorAll() {
        const results = [];
        const walk = e => { if (e.type === 'number' && !e.disabled) results.push(e); e.children.forEach(walk); };
        walk(this); return results;
    }
}
const ids = Object.fromEntries(['profile-status', 'profile-fields', 'profile-bonuses', 'profile-keys',
    'profile-customizations', 'profile-class', 'profile-kind', 'profile-search', 'profile-custom-count',
    'btn-profile-save', 'btn-profile-load', 'btn-open-account', 'editor', 'no-save-msg', 'mtab-profile'].map(id => [id, new Element()]));
ids['profile-class'].value = 'Axton'; ids['profile-kind'].value = 'head';
ids['mtab-profile'].append(ids['profile-fields'], ids['profile-bonuses'], ids['profile-keys']);
const nav = new Element();
const profile = {
    revision: 'synthetic-revision', rank: 100, tokens: 3, fov: 90,
    bonuses: Array(14).fill(1), bonus_percentages: Array(14).fill(1),
    bonus_names: Array.from({length: 14}, (_, i) => 'Bonus ' + i), keys: [{source: 0, available: 4, used: 2}],
    customizations: [{asset: 'head.A', class: 'Axton', kind: 'head', name: 'Head A', editable: true, owned: false},
                     {asset: 'skin.A', class: 'Axton', kind: 'skin', name: 'Skin A', editable: true, owned: true}],
    warnings: ['Synthetic data only']
};
let consent = true, posts = [];
const context = {
    document: {getElementById: id => ids[id], createElement: tag => new Element(tag), querySelector: () => nav},
    _mutating: false, confirm: () => consent,
    api: async (url, opts) => {
        assert.equal(url, '/api/profile');
        if (!opts) return profile;
        posts.push(opts); return {changed: true, profile};
    }, console
};
vm.createContext(context); vm.runInContext(fs.readFileSync('static/profile.js', 'utf8'), context);
(async () => {
    await ids['btn-profile-load'].click();
    assert.equal(ids['profile-bonuses'].children.length, 14);
    assert.equal(ids['btn-profile-save'].disabled, false);
    let checkbox = ids['profile-customizations'].children[0].children[0];
    checkbox.checked = true; await checkbox.fire('change');
    ids['profile-kind'].value = 'skin'; await ids['profile-kind'].fire('input');
    ids['profile-kind'].value = 'head'; await ids['profile-kind'].fire('input');
    assert.equal(ids['profile-customizations'].children[0].children[0].checked, true);
    consent = false; await ids['btn-profile-save'].click(); assert.equal(posts.length, 0);
    consent = true; await ids['btn-profile-save'].click(); assert.equal(posts.length, 1);
    const body = posts[0].body;
    assert.equal(body.confirm_profile_write, true); assert.equal(body.revision, 'synthetic-revision');
    assert.equal(body.changes.customizations['head.A'], true); assert.equal(body.changes.keys['0'], 4);
    assert.equal(body.changes.bonuses.length, 14); assert.equal(context._mutating, false);
    assert.match(ids['profile-status'].textContent, /saved and verified/);
    // Run the actual class-change listener with the same synthetic DOM.
    ['btn-change-class', 'char-class-select', 'class-unlock-heads', 'class-change-head-results'].forEach(id => { ids[id] = new Element(); });
    ids['char-class-select'].value = 'Maya'; ids['class-unlock-heads'].checked = true;
    context.currentFile = 'Save0001.sav'; context.currentData = {character: {class_name: 'Gaige'}};
    context._skillEdits = {}; context.loadSave = async () => {}; context.loadSaveList = async () => {};
    context.toast = () => {};
    const confirmations = [];
    context.confirm = message => { confirmations.push(message); return consent; };
    context.api = async (url, opts) => {
        assert.equal(url, '/api/save/Save0001.sav/class'); posts.push(opts);
        return {refunded_points: 19, heads: opts.body.unlock_heads ? {
            unlocked_count: 2, target_owned_before: 0, target_owned_after: 2,
            mappings: [{source: 'Read All About It', target: 'One-Eye Spy', tier: 'similar', already_owned: false}]
        } : null, warnings: ['Synthetic warning']};
    };
    const source = fs.readFileSync('static/app.js', 'utf8');
    const start = source.indexOf('document.getElementById("btn-change-class").addEventListener');
    const end = source.indexOf('document.getElementById("btn-save-skills").addEventListener', start);
    vm.runInContext(source.slice(start, end), context);
    consent = false; await ids['btn-change-class'].click(); assert.equal(posts.length, 1);
    consent = true; await ids['btn-change-class'].click(); assert.equal(posts.length, 2);
    assert.equal(posts[1].body.unlock_heads, true); assert.equal(posts[1].body.confirm_profile_write, true);
    assert.match(confirmations.at(-1), /account-wide profile.bin/);
    assert.match(ids['class-change-head-results'].children[1].textContent, /One-Eye Spy.*similar/);
    ids['class-unlock-heads'].checked = false; await ids['btn-change-class'].click();
    assert.equal(posts[2].body.confirm_profile_write, false);
    assert.match(ids['class-change-head-results'].children[0].textContent, /unchanged/);
    console.log('Account/class UI smoke passed: pending head/skin filters, BAR/key controls, explicit consent, mapping report, save-only option.');
})().catch(error => { console.error(error); process.exitCode = 1; });
