// Log-scale minute sliders for the presence duration fields (issue #59).
//
// Every input tagged with data-duration-slider gains a range slider and a
// small read-out of the whole-minute value. The slider maps its 0..100
// positions logarithmically onto 1..60 minutes, so the low end (a few
// minutes) gets most of the travel. The text input stays authoritative:
// values typed into it reposition the thumb (clamped at either end), so
// durations beyond an hour remain possible — the read-out then shows the
// true minute count even though the thumb sits at the maximum.
(function () {
  "use strict";

  const MIN_MINUTES = 1;
  const MAX_MINUTES = 60;
  const STEPS = 100;
  const LOG_RANGE = Math.log(MAX_MINUTES / MIN_MINUTES);

  function minutesFromPosition(position) {
    return Math.round(MIN_MINUTES * Math.exp((position / STEPS) * LOG_RANGE));
  }

  function positionFromMinutes(minutes) {
    const clamped = Math.min(Math.max(minutes, MIN_MINUTES), MAX_MINUTES);
    return Math.round((STEPS * Math.log(clamped / MIN_MINUTES)) / LOG_RANGE);
  }

  // Parse a Django-style duration string to whole minutes (rounded), using
  // Django's own colon rules: SS, MM:SS or HH:MM:SS. Returns null when the
  // text is not (yet) a duration in that form.
  function minutesFromText(text) {
    const parts = text.trim().split(":");
    if (parts.length < 1 || parts.length > 3) {
      return null;
    }
    const numbers = parts.map(function (part) {
      return part === "" ? NaN : Number(part);
    });
    if (numbers.some(Number.isNaN)) {
      return null;
    }
    let seconds = 0;
    numbers.forEach(function (value) {
      seconds = seconds * 60 + value;
    });
    return Math.round(seconds / 60);
  }

  function textFromMinutes(minutes) {
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return (
      String(hours).padStart(2, "0") +
      ":" +
      String(remainder).padStart(2, "0") +
      ":00"
    );
  }

  function attachSlider(input) {
    const wrapper = document.createElement("div");
    wrapper.className = "d-flex align-items-center gap-2 mt-1";

    const slider = document.createElement("input");
    slider.type = "range";
    slider.className = "form-range";
    slider.min = "0";
    slider.max = String(STEPS);
    slider.step = "1";
    slider.setAttribute("aria-label", "Minutes for " + input.id);

    const readout = document.createElement("output");
    readout.className = "badge text-bg-secondary";
    readout.style.minWidth = "4.5em";
    readout.htmlFor = input.id;

    wrapper.appendChild(slider);
    wrapper.appendChild(readout);
    input.insertAdjacentElement("afterend", wrapper);

    function syncFromInput() {
      const minutes = minutesFromText(input.value);
      if (minutes === null) {
        readout.textContent = "—";
        return;
      }
      slider.value = String(positionFromMinutes(minutes));
      readout.textContent = minutes + " min";
    }

    slider.addEventListener("input", function () {
      const minutes = minutesFromPosition(Number(slider.value));
      input.value = textFromMinutes(minutes);
      readout.textContent = minutes + " min";
    });
    input.addEventListener("input", syncFromInput);
    syncFromInput();
  }

  document
    .querySelectorAll("input[data-duration-slider]")
    .forEach(attachSlider);
})();
