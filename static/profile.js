/* Account data deliberately has its own pending edits and explicit write consent. */
(function() {
    "use strict";
    let profile = null;
    let pendingCustomizations = {};
    const byId = id => document.getElementById(id);
    function status(text) { byId("profile-status").textContent = text; }
    function numberInput(container, labelText, value, min, max, field, index) {
        const label = document.createElement("label");
        label.className = "profile-number";
        const caption = document.createElement("span"); caption.textContent = labelText;
        const input = document.createElement("input");
        input.type = "number"; input.step = "1"; input.min = min; input.max = max;
        input.dataset.field = field;
        if (index !== undefined) input.dataset.index = index;
        input.value = value === null ? "" : value;
        input.disabled = value === null;
        input.setAttribute("aria-label", labelText);
        label.append(caption, input); container.appendChild(label);
        return input;
    }
    function renderCustomizations() {
        const container = byId("profile-customizations"); container.replaceChildren();
        if (!profile) return;
        const rows = profile.customizations.filter(d => d.class === byId("profile-class").value &&
            d.kind === byId("profile-kind").value);
        const owned = d => Object.hasOwn(pendingCustomizations, d.asset) ? pendingCustomizations[d.asset] : d.owned;
        byId("profile-custom-count").textContent = rows.filter(d => owned(d) === true).length +
            " known owned; pack entitlement not counted";
        const query = byId("profile-search").value.toLowerCase();
        rows.filter(d => d.name.toLowerCase().includes(query)).forEach(d => {
            const label = document.createElement("label"); label.className = "profile-custom";
            const checkbox = document.createElement("input"); checkbox.type = "checkbox";
            checkbox.checked = owned(d) === true; checkbox.disabled = !d.editable;
            checkbox.addEventListener("change", () => {
                if (checkbox.checked === d.owned) delete pendingCustomizations[d.asset];
                else pendingCustomizations[d.asset] = checkbox.checked;
                renderCustomizations();
            });
            const text = document.createElement("span");
            text.textContent = d.name + (d.editable ? "" : " [" + d.availability + "; read-only]");
            label.append(checkbox, text); container.appendChild(label);
        });
    }
    function render() {
        byId("profile-fields").replaceChildren(); byId("profile-bonuses").replaceChildren(); byId("profile-keys").replaceChildren();
        numberInput(byId("profile-fields"), "Badass Rank", profile.rank, 0, 429496729, "rank");
        numberInput(byId("profile-fields"), "Available BAR tokens", profile.tokens, 0, 62530, "tokens");
        numberInput(byId("profile-fields"), "Field of view", profile.fov, 60, 120, "fov");
        profile.bonus_names.forEach((name, i) => {
            const value = profile.bonuses === null ? null : profile.bonuses[i];
            const input = numberInput(byId("profile-bonuses"), name + " (" +
                (value === null ? "unavailable" : profile.bonus_percentages[i] + "%") + ")", value, 0, 62530, "bonuses", i);
            input.addEventListener("input", () => {
                input.previousSibling.textContent = name + " (" + (Math.max(0, Number(input.value)) ** .75).toFixed(1) + "%)";
            });
        });
        (profile.keys || []).forEach(key => {
            const names = {0: "SHiFT", 173: "Mechromancer", 254: "Premier Club"};
            numberInput(byId("profile-keys"), (names[key.source] || "Source " + key.source) +
                " available (" + key.used + " used)", key.available, 0, 255 - key.used, "keys", key.source);
        });
        renderCustomizations();
        byId("btn-profile-save").disabled = false;
    }
    async function load() {
        if (_mutating) { status("Wait for the current write to finish."); return; }
        if (profile && !confirm("Reload profile and discard pending account edits?")) return;
        byId("btn-profile-save").disabled = true;
        status("Reading profile.bin…");
        try {
            profile = await api("/api/profile"); pendingCustomizations = {};
            render(); status(profile.warnings.join(" "));
        } catch (e) { profile = null; status("Profile unavailable: " + e.message); }
    }
    byId("btn-profile-load").addEventListener("click", load);
    document.querySelector('[data-mtab="profile"]').addEventListener("click", () => { if (!profile) load(); });
    byId("btn-open-account").addEventListener("click", () => {
        byId("editor").classList.remove("hidden"); byId("no-save-msg").classList.add("hidden");
        document.querySelector('[data-mtab="profile"]').click();
    });
    ["profile-class", "profile-kind", "profile-search"].forEach(id => byId(id).addEventListener("input", renderCustomizations));
    byId("btn-profile-save").addEventListener("click", async function() {
        if (!profile || _mutating) return;
        const changes = {customizations: pendingCustomizations};
        const bonuses = []; const keys = {};
        let valid = true;
        byId("mtab-profile").querySelectorAll('input[type="number"]:not(:disabled)').forEach(input => {
            if (!input.checkValidity() || input.value === "" || !Number.isSafeInteger(Number(input.value))) { input.reportValidity(); valid = false; return; }
            const n = Number(input.value), field = input.dataset.field;
            if (field === "bonuses") bonuses[Number(input.dataset.index)] = n;
            else if (field === "keys") keys[input.dataset.index] = n;
            else changes[field] = n;
        });
        if (!valid) return;
        if (bonuses.length) changes.bonuses = bonuses;
        if (Object.keys(keys).length) changes.keys = keys;
        if (!confirm("Write these ACCOUNT-WIDE profile.bin changes for every character? Unlocks may be added or removed as selected. BAR/key edits take effect immediately. A rotating .bak backup will be created. Close Borderlands 2 first.")) return;
        _mutating = true; this.disabled = true;
        try {
            const result = await api("/api/profile", {method: "POST", body: {
                changes, revision: profile.revision, confirm_profile_write: true
            }});
            profile = result.profile; pendingCustomizations = {}; render();
            status(result.changed ? "Profile saved and verified; previous profile is in profile.bin.bak." : "No changes; profile and backups untouched.");
        } catch (e) { status("Account write failed: " + e.message + " Reload if the profile revision changed."); }
        finally { _mutating = false; this.disabled = false; }
    });
})();
