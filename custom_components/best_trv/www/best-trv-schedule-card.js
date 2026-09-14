/**
 * Best TRV schedule card.
 *
 * A visual weekly heating/cooling schedule editor for a Best TRV climate
 * entity, shown as a horizontal segmented bar per day with draggable
 * slot markers - the alternative to the config-flow's own per-day form.
 * Both write the exact same `schedule` config-entry key; this card just
 * talks to it through three entity services (best_trv.set_schedule_day,
 * set_schedule_days, set_schedule_enabled) instead of an options flow.
 *
 * The draggable-marker *interaction concept* is inspired by the (GPLv3)
 * nielsfaber/scheduler-card, but every line here is original: no source
 * from that project was copied or adapted, so this card carries no
 * copyleft obligation of its own.
 *
 * Deliberately buildless (no bundler/TypeScript/npm) - self-registered by
 * custom_components/best_trv/__init__.py via Home Assistant's own
 * add_extra_js_url, so installing the integration is enough; no separate
 * HACS "plugin" or manual Lovelace-resource step is needed.
 */
(function () {
  const CARD_TAG = "best-trv-schedule-card";
  const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const MAX_SLOTS_PER_DAY = 12;
  // Exact brand gradient from tools/generate_icon.py, so the card's
  // warm/cool segment coloring visually matches the integration's own icon.
  const WARM = [255, 87, 51];
  const COOL = [37, 130, 255];

  const I18N = {
    en: {
      days: { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" },
      schedule: "Schedule",
      noSlots: "No slots yet",
      addSlot: "Add slot",
      copyFrom: "Copy from",
      pushTo: "Push to",
      apply: "Apply",
      delete: "Delete",
      time: "Time",
      temperature: "Temperature",
      chooseDay: "Choose a day",
    },
    nl: {
      days: { mon: "Maandag", tue: "Dinsdag", wed: "Woensdag", thu: "Donderdag", fri: "Vrijdag", sat: "Zaterdag", sun: "Zondag" },
      schedule: "Schema",
      noSlots: "Nog geen sloten",
      addSlot: "Slot toevoegen",
      copyFrom: "Kopieer van",
      pushTo: "Push naar",
      apply: "Toepassen",
      delete: "Verwijder",
      time: "Tijd",
      temperature: "Temperatuur",
      chooseDay: "Kies een dag",
    },
  };

  function segColor(temp, min, max) {
    const span = max - min || 1;
    const f = Math.max(0, Math.min(1, (temp - min) / span));
    const rgb = WARM.map((w, i) => Math.round(COOL[i] * (1 - f) + w * f));
    return "rgb(" + rgb.join(",") + ")";
  }

  function toMinutes(hhmmss) {
    const parts = String(hhmmss).split(":").map(Number);
    return (parts[0] || 0) * 60 + (parts[1] || 0);
  }

  function fromMinutes(min) {
    min = ((Math.round(min) % 1440) + 1440) % 1440;
    const h = Math.floor(min / 60);
    const m = min % 60;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":00";
  }

  function sortedSlots(slots) {
    return [...(slots || [])].sort((a, b) => toMinutes(a.time) - toMinutes(b.time));
  }

  function suggestNextSlot(slots, tempMin) {
    const sorted = sortedSlots(slots);
    if (!sorted.length) return { time: "00:00:00", temperature: tempMin };
    const last = sorted[sorted.length - 1];
    return { time: fromMinutes(toMinutes(last.time) + 60), temperature: last.temperature };
  }

  class BestTrvScheduleCard extends HTMLElement {
    static getStubConfig(hass) {
      const entityId = Object.keys(hass.states).find(
        (id) => id.startsWith("climate.") && hass.states[id].attributes.schedule !== undefined
      );
      return { entity: entityId || "" };
    }

    setConfig(config) {
      if (!config || !config.entity) {
        throw new Error("Please define a Best TRV climate entity");
      }
      this._config = config;
      this._openDay = null; // which day's detail (bar + panel) is expanded
      this._editingIndex = null; // index of the slot being edited within _openDay
      this._pushOpen = false; // whether the "push to" picker is open
      this._copyOpen = false; // whether the "copy from" picker is open
      this._pushSelection = [];
      this._dragging = null; // {day, index, barEl, slots} while a marker drag is in progress
      this._lastSnapshot = null;
      if (!this._root) {
        this._root = this;
      }
    }

    getCardSize() {
      return 2 + DAY_KEYS.length;
    }

    set hass(hass) {
      this._hass = hass;
      const stateObj = hass.states[this._config.entity];
      if (!stateObj) {
        this._root.innerHTML =
          '<ha-card><div style="padding:16px;color:var(--error-color, red);">Entity not found: ' +
          this._config.entity +
          "</div></ha-card>";
        return;
      }
      this._stateObj = stateObj;

      // Skip re-rendering unless the schedule-relevant attributes actually
      // changed, or nothing has been rendered yet - the entity's own
      // async_write_ha_state fires roughly every 30s regardless of the
      // schedule (feed temperature, hvac_action, etc.), and a naive full
      // re-render on every one of those would close any inline editor the
      // user has open mid-edit. An open drag or edit panel additionally
      // blocks re-rendering outright, regardless of the snapshot.
      const attrs = stateObj.attributes;
      const snapshot = JSON.stringify([
        attrs.schedule,
        attrs.schedule_enabled,
        attrs.schedule_temp_min,
        attrs.schedule_temp_max,
        attrs.friendly_name,
      ]);
      if (this._dragging || this._editingIndex !== null) return;
      if (snapshot === this._lastSnapshot) return;
      this._lastSnapshot = snapshot;
      this._render();
    }

    _t() {
      const lang = (this._hass && this._hass.language) || "en";
      return I18N[lang] || I18N.en;
    }

    _schedule() {
      return (this._stateObj.attributes.schedule || {});
    }

    _tempBounds() {
      const attrs = this._stateObj.attributes;
      return [attrs.schedule_temp_min != null ? attrs.schedule_temp_min : 15, attrs.schedule_temp_max != null ? attrs.schedule_temp_max : 27];
    }

    _call(service, data) {
      this._hass.callService("best_trv", service, Object.assign({ entity_id: this._config.entity }, data));
    }

    _setDay(day, slots) {
      this._call("set_schedule_day", { day, slots });
    }

    _render() {
      const t = this._t();
      const attrs = this._stateObj.attributes;
      const schedule = this._schedule();
      const [tempMin, tempMax] = this._tempBounds();

      const card = document.createElement("ha-card");
      card.style.padding = "16px";

      const header = document.createElement("div");
      header.style.cssText = "display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;";
      const title = document.createElement("div");
      title.innerHTML =
        '<div style="font-size:16px;font-weight:500;color:var(--primary-text-color);">' +
        (attrs.friendly_name || this._config.entity) +
        '</div><div style="font-size:12px;color:var(--secondary-text-color);">' +
        t.schedule +
        "</div>";
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = !!attrs.schedule_enabled;
      toggle.style.cssText = "width:36px;height:20px;cursor:pointer;";
      toggle.addEventListener("change", () => this._call("set_schedule_enabled", { enabled: toggle.checked }));
      header.appendChild(title);
      header.appendChild(toggle);
      card.appendChild(header);

      DAY_KEYS.forEach((day) => {
        card.appendChild(this._renderDayRow(day, schedule, tempMin, tempMax, t));
      });

      this._root.innerHTML = "";
      this._root.appendChild(card);
    }

    _renderDayRow(day, schedule, tempMin, tempMax, t) {
      const isOpen = this._openDay === day;
      const slots = sortedSlots(schedule[day]);

      const row = document.createElement("div");
      row.style.cssText = "margin-bottom:8px;";

      const head = document.createElement("div");
      head.style.cssText = "display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer;";
      const label = document.createElement("span");
      label.textContent = t.days[day];
      label.style.cssText = "font-size:13px;width:78px;flex-shrink:0;color:var(--primary-text-color);";
      const bar = document.createElement("div");
      bar.style.cssText =
        "flex:1;height:18px;border-radius:9px;overflow:visible;position:relative;background:var(--divider-color);";
      const chevron = document.createElement("span");
      chevron.textContent = isOpen ? "▴" : "▾";
      chevron.style.cssText = "font-size:12px;color:var(--secondary-text-color);width:14px;text-align:center;";

      this._paintBar(bar, day, slots, tempMin, tempMax);

      head.addEventListener("click", (e) => {
        if (e.target.closest("[data-marker]")) return; // markers handle their own click
        this._openDay = isOpen ? null : day;
        this._editingIndex = null;
        this._copyOpen = false;
        this._pushOpen = false;
        this._render();
      });

      head.appendChild(label);
      head.appendChild(bar);
      head.appendChild(chevron);
      row.appendChild(head);

      if (isOpen) {
        row.appendChild(this._renderDayPanel(day, slots, tempMin, tempMax, t));
      }
      return row;
    }

    _paintBar(bar, day, slots, tempMin, tempMax) {
      bar.innerHTML = "";
      if (!slots.length) return;
      const addSeg = (startPct, widthPct, color) => {
        const seg = document.createElement("div");
        seg.style.cssText =
          "position:absolute;top:0;bottom:0;left:" + startPct + "%;width:" + widthPct + "%;background:" + color + ";border-radius:9px;";
        bar.appendChild(seg);
      };
      // The stretch before the first slot's own start time is still
      // showing whatever the *last* slot set - it carried over from
      // yesterday, wrapping past midnight - so it gets that slot's color
      // rather than being left blank. Drawing it first (then each slot's
      // own segment ending at 1440, not wrapping past it) keeps every
      // segment within the bar's own width instead of overflowing past
      // 100% the way a naive "next slot, or first slot + 1440" span would.
      const firstStart = toMinutes(slots[0].time);
      if (firstStart > 0) {
        addSeg(0, (firstStart / 1440) * 100, segColor(slots[slots.length - 1].temperature, tempMin, tempMax));
      }
      slots.forEach((slot, i) => {
        const start = toMinutes(slot.time);
        const end = i + 1 < slots.length ? toMinutes(slots[i + 1].time) : 1440;
        const pct = ((end - start) / 1440) * 100;
        addSeg((start / 1440) * 100, pct, segColor(slot.temperature, tempMin, tempMax));
      });
      slots.forEach((slot, i) => {
        // A wider, invisible hit-area (14px) around the thin 4px visual
        // line - the visual marker alone is too thin to reliably tap on a
        // touchscreen, and even a mouse click has some slack.
        const marker = document.createElement("div");
        marker.dataset.marker = "1";
        marker.title = slot.time.slice(0, 5) + " → " + slot.temperature + "°C";
        marker.style.cssText =
          "position:absolute;top:-6px;bottom:-6px;left:" +
          (toMinutes(slot.time) / 1440) * 100 +
          "%;width:14px;margin-left:-7px;cursor:grab;display:flex;align-items:center;justify-content:center;";
        const handle = document.createElement("div");
        handle.style.cssText =
          "width:4px;height:calc(100% - 8px);background:#fff;border-radius:2px;box-shadow:0 0 0 1px rgba(0,0,0,.3);pointer-events:none;";
        marker.appendChild(handle);
        marker.addEventListener("pointerdown", (e) => this._startDrag(e, day, i, bar, slots));
        marker.addEventListener("click", (e) => {
          e.stopPropagation();
          if (this._justDragged) {
            this._justDragged = false;
            return;
          }
          this._openDay = day;
          this._editingIndex = this._editingIndex === i ? null : i;
          this._render();
        });
        bar.appendChild(marker);
      });
    }

    _startDrag(e, day, index, barEl, slots) {
      e.preventDefault();
      e.stopPropagation();
      const marker = e.currentTarget;
      marker.setPointerCapture(e.pointerId);
      const startX = e.clientX;
      // A real mouse/trackpad reports a pixel or two of movement on
      // almost every plain click - without a deadzone, that movement got
      // treated as "the user is dragging", which committed a near-zero
      // time change AND suppressed the click that should have opened the
      // slot editor. Below this threshold nothing happens yet; crossing
      // it is what actually starts the visual drag.
      const DRAG_THRESHOLD_PX = 4;
      this._dragging = { day, index, marker, barEl, slots: slots.map((s) => ({ ...s })), moved: false };

      const onMove = (ev) => {
        const d = this._dragging;
        if (!d) return;
        if (!d.moved && Math.abs(ev.clientX - startX) < DRAG_THRESHOLD_PX) return;
        d.moved = true;
        const rect = d.barEl.getBoundingClientRect();
        let pct = (ev.clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(0.999, pct));
        let minutes = Math.round((pct * 1440) / 15) * 15; // snap to 15 minutes
        d.slots[d.index] = Object.assign({}, d.slots[d.index], { time: fromMinutes(minutes) });
        this._paintBar(d.barEl, d.day, sortedSlots(d.slots), ...this._tempBounds());
      };
      const onUp = () => {
        const d = this._dragging;
        this._dragging = null;
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        if (!d) return;
        if (d.moved) {
          this._justDragged = true;
          this._setDay(d.day, sortedSlots(d.slots));
        }
      };
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    }

    _renderDayPanel(day, slots, tempMin, tempMax, t) {
      const panel = document.createElement("div");
      panel.style.cssText =
        "margin-top:6px;background:var(--secondary-background-color);border-radius:8px;padding:10px 12px;";

      if (this._editingIndex !== null && slots[this._editingIndex]) {
        panel.appendChild(this._renderSlotEditor(day, slots, this._editingIndex, t));
      } else if (!slots.length) {
        const empty = document.createElement("div");
        empty.textContent = t.noSlots;
        empty.style.cssText = "font-size:12px;color:var(--secondary-text-color);margin-bottom:8px;";
        panel.appendChild(empty);
      }

      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;gap:14px;flex-wrap:wrap;margin-top:6px;";

      const addBtn = this._actionLink(t.addSlot, () => {
        if (slots.length >= MAX_SLOTS_PER_DAY) return;
        const next = suggestNextSlot(slots, tempMin);
        this._setDay(day, [...slots, next]);
      });
      const copyBtn = this._actionLink(t.copyFrom, () => {
        this._copyOpen = !this._copyOpen;
        this._pushOpen = false;
        this._render();
      });
      const pushBtn = this._actionLink(t.pushTo, () => {
        this._pushOpen = !this._pushOpen;
        this._copyOpen = false;
        this._render();
      });
      actions.appendChild(addBtn);
      actions.appendChild(copyBtn);
      actions.appendChild(pushBtn);
      panel.appendChild(actions);

      if (this._copyOpen) panel.appendChild(this._renderCopyFrom(day, t));
      if (this._pushOpen) panel.appendChild(this._renderPushTo(day, slots, t));

      return panel;
    }

    _actionLink(label, onClick) {
      const el = document.createElement("span");
      el.textContent = label;
      el.style.cssText = "font-size:12px;color:var(--accent-color, var(--primary-color));cursor:pointer;";
      el.addEventListener("click", onClick);
      return el;
    }

    _renderSlotEditor(day, slots, index, t) {
      const slot = slots[index];
      const wrap = document.createElement("div");
      wrap.style.cssText = "display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px;";

      const timeInput = document.createElement("input");
      timeInput.type = "time";
      timeInput.value = slot.time.slice(0, 5);
      timeInput.style.cssText = "background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;padding:4px 6px;font-size:13px;";

      const tempInput = document.createElement("input");
      tempInput.type = "number";
      tempInput.step = "0.5";
      tempInput.value = slot.temperature;
      tempInput.style.cssText = "width:64px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;padding:4px 6px;font-size:13px;";

      const commit = () => {
        const updated = slots.map((s, i) =>
          i === index ? { time: (timeInput.value || "00:00") + ":00", temperature: parseFloat(tempInput.value) || 0 } : s
        );
        this._setDay(day, updated);
        this._editingIndex = null;
      };
      timeInput.addEventListener("change", commit);
      tempInput.addEventListener("change", commit);

      // A plain inline SVG, not an icon-font class (e.g. Tabler's `ti
      // ti-trash`) - Home Assistant's own frontend doesn't load that font,
      // so a font-based icon here silently rendered as nothing. SVG has no
      // such dependency: it looks the same everywhere.
      const delBtn = document.createElement("button");
      delBtn.setAttribute("aria-label", t.delete);
      delBtn.title = t.delete;
      delBtn.style.cssText =
        "background:none;border:none;padding:4px;margin-left:auto;cursor:pointer;color:var(--error-color, #c46060);display:flex;align-items:center;";
      delBtn.innerHTML =
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
      delBtn.addEventListener("click", () => {
        this._setDay(day, slots.filter((_, i) => i !== index));
        this._editingIndex = null;
      });

      wrap.appendChild(timeInput);
      wrap.appendChild(tempInput);
      const unit = document.createElement("span");
      unit.textContent = "°C";
      unit.style.cssText = "font-size:12px;color:var(--secondary-text-color);";
      wrap.appendChild(unit);
      wrap.appendChild(delBtn);
      return wrap;
    }

    _renderCopyFrom(day, t) {
      const wrap = document.createElement("div");
      wrap.style.cssText = "margin-top:8px;";
      const select = document.createElement("select");
      select.style.cssText = "font-size:13px;padding:4px 6px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;";
      const placeholder = document.createElement("option");
      placeholder.textContent = t.chooseDay;
      placeholder.value = "";
      select.appendChild(placeholder);
      DAY_KEYS.filter((d) => d !== day).forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d;
        opt.textContent = t.days[d];
        select.appendChild(opt);
      });
      select.addEventListener("change", () => {
        if (!select.value) return;
        const sourceSlots = this._schedule()[select.value] || [];
        this._setDay(day, sourceSlots);
        this._copyOpen = false;
      });
      wrap.appendChild(select);
      return wrap;
    }

    _renderPushTo(day, slots, t) {
      const wrap = document.createElement("div");
      wrap.style.cssText = "margin-top:8px;display:flex;flex-direction:column;gap:4px;";
      const chosen = new Set();
      DAY_KEYS.filter((d) => d !== day).forEach((d) => {
        const label = document.createElement("label");
        label.style.cssText = "font-size:13px;display:flex;align-items:center;gap:6px;color:var(--primary-text-color);cursor:pointer;";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.addEventListener("change", () => {
          if (cb.checked) chosen.add(d);
          else chosen.delete(d);
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(t.days[d]));
        wrap.appendChild(label);
      });
      const applyBtn = document.createElement("button");
      applyBtn.textContent = t.apply;
      applyBtn.style.cssText = "align-self:flex-start;margin-top:4px;font-size:12px;padding:4px 10px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer;";
      applyBtn.addEventListener("click", () => {
        if (!chosen.size) return;
        this._call("set_schedule_days", { days: [...chosen], slots });
        this._pushOpen = false;
        this._render();
      });
      wrap.appendChild(applyBtn);
      return wrap;
    }
  }

  if (!customElements.get(CARD_TAG)) {
    customElements.define(CARD_TAG, BestTrvScheduleCard);
  }
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: "Best TRV schedule",
    description: "Visual weekly heating/cooling schedule editor for a Best TRV room.",
  });
})();
