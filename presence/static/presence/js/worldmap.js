/*
 * World map page: places one marker per location (positions computed
 * server-side from the astral city database) and overlays the day/night
 * terminator, both on the 1000x500 equirectangular map in map.html.
 *
 * The subsolar point comes from the standard NOAA low-accuracy solar
 * position formulas (a fraction of a degree of error — invisible at this
 * map scale). Zooming works by shrinking the SVG viewBox (so coastlines
 * stay vector-crisp) and repositioning the HTML markers relative to the
 * visible window (so labels keep a constant on-screen size). No
 * dependencies.
 */
(function () {
  "use strict";

  var RAD = Math.PI / 180;
  var MAP_WIDTH = 1000;
  var MAP_HEIGHT = 500;
  var MIN_VIEW_WIDTH = MAP_WIDTH / 8; /* max zoom: 8x */

  function projectX(longitude) {
    return ((longitude + 180) / 360) * MAP_WIDTH;
  }

  function projectY(latitude) {
    return ((90 - latitude) / 180) * MAP_HEIGHT;
  }

  /* The point on Earth where the sun is directly overhead. */
  function subsolarPoint(date) {
    var daysSinceJ2000 = date.getTime() / 86400000 + 2440587.5 - 2451545.0;
    var meanAnomaly = (357.529 + 0.98560028 * daysSinceJ2000) % 360;
    var meanLongitude = (280.459 + 0.98564736 * daysSinceJ2000) % 360;
    var eclipticLongitude =
      meanLongitude +
      1.915 * Math.sin(meanAnomaly * RAD) +
      0.02 * Math.sin(2 * meanAnomaly * RAD);
    var obliquity = 23.439 - 0.00000036 * daysSinceJ2000;

    var declination =
      Math.asin(Math.sin(obliquity * RAD) * Math.sin(eclipticLongitude * RAD)) /
      RAD;
    var rightAscension =
      Math.atan2(
        Math.cos(obliquity * RAD) * Math.sin(eclipticLongitude * RAD),
        Math.cos(eclipticLongitude * RAD)
      ) / RAD;
    /* Equation of time, as degrees of hour angle (wrapped to ±180). */
    var equationOfTime =
      ((meanLongitude - rightAscension) % 360 + 540) % 360 - 180;

    var utcHours =
      date.getUTCHours() +
      date.getUTCMinutes() / 60 +
      date.getUTCSeconds() / 3600;
    var longitude = -15 * (utcHours - 12) - equationOfTime;
    longitude = ((longitude + 540) % 360) - 180;
    return { latitude: declination, longitude: longitude };
  }

  /*
   * Night polygon: the terminator latitude is single-valued in longitude,
   * and at every longitude the night side runs from that curve to the pole
   * opposite the sun — so tracing the curve across the map and closing the
   * path along that pole's edge shades exactly the night region.
   */
  function nightPathData(date) {
    var sun = subsolarPoint(date);
    /* Near the equinox tan(declination) → 0; clamp to keep the latitude
       formula finite (the visual difference is under half a pixel). */
    var declination =
      Math.abs(sun.latitude) < 0.05
        ? (sun.latitude < 0 ? -0.05 : 0.05)
        : sun.latitude;
    var darkPoleY = declination > 0 ? MAP_HEIGHT : 0;

    var d = "";
    for (var longitude = -180; longitude <= 180; longitude += 2) {
      var hourAngle = (longitude - sun.longitude) * RAD;
      var latitude =
        Math.atan(-Math.cos(hourAngle) / Math.tan(declination * RAD)) / RAD;
      d +=
        (d ? "L" : "M") +
        projectX(longitude).toFixed(1) +
        " " +
        projectY(latitude).toFixed(1);
    }
    d += "L" + MAP_WIDTH + " " + darkPoleY + "L0 " + darkPoleY + "Z";
    return d;
  }

  var wrap = document.getElementById("map-wrap");
  var svg = wrap.querySelector("svg");
  var shadow = document.getElementById("night-shadow");
  var utcCaption = document.getElementById("map-utc");
  var zoomInButton = document.getElementById("map-zoom-in");
  var zoomOutButton = document.getElementById("map-zoom-out");
  var zoomResetButton = document.getElementById("map-zoom-reset");
  var locations = JSON.parse(
    document.getElementById("map-locations").textContent
  );

  var utcFormatter = new Intl.DateTimeFormat(undefined, {
    dateStyle: "full",
    timeStyle: "short",
    timeZone: "UTC",
  });

  function timeFormatter(timeZone) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: timeZone,
      });
    } catch (error) {
      /* An IANA zone this browser doesn't know: show the marker without
         a time rather than dropping the location from the map. */
      return null;
    }
  }

  /* "1 on · 2 off · 1 disabled" (empty for a location with no presences). */
  function presenceSummary(counts) {
    var parts = [];
    if (counts.on_count) {
      parts.push(counts.on_count + " on");
    }
    if (counts.off_count) {
      parts.push(counts.off_count + " off");
    }
    if (counts.disabled_count) {
      parts.push(counts.disabled_count + " disabled");
    }
    return parts.join(" · ");
  }

  function applyStatus(marker, entry) {
    marker.dot.className = "map-marker-dot status-" + entry.status;
    var summary = presenceSummary(entry);
    marker.anchor.title =
      marker.city +
      " · " +
      marker.timezone +
      (summary ? " — " + summary : " — no presences");
  }

  var markersById = {};

  var markers = locations.map(function (location) {
    var anchor = document.createElement("a");
    anchor.className = "map-marker";
    anchor.href = location.url;

    var label = document.createElement("span");
    label.className = "map-marker-label";
    var dot = document.createElement("span");
    dot.className = "map-marker-dot";
    anchor.appendChild(label);
    anchor.appendChild(dot);
    wrap.appendChild(anchor);

    var marker = {
      name: location.name,
      city: location.city,
      timezone: location.timezone,
      x: projectX(location.longitude),
      y: projectY(location.latitude),
      formatter: timeFormatter(location.timezone),
      anchor: anchor,
      label: label,
      dot: dot,
    };
    applyStatus(marker, location);
    markersById[location.id] = marker;
    return marker;
  });

  function refreshTimes() {
    var now = new Date();
    markers.forEach(function (marker) {
      marker.label.textContent = marker.formatter
        ? marker.name + " " + marker.formatter.format(now)
        : marker.name;
    });
    if (utcCaption) {
      utcCaption.textContent = utcFormatter.format(now) + " UTC";
    }
  }

  function refreshShadow() {
    shadow.setAttribute("d", nightPathData(new Date()));
  }

  /* Re-fetch marker statuses so dot colours track the runner's on/off
     flips. A failed poll (network blip, expired session redirecting to
     the login page) keeps the current colours and retries next cycle. */
  function refreshStatuses() {
    fetch(wrap.dataset.statusUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("status poll failed: HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        payload.locations.forEach(function (entry) {
          var marker = markersById[entry.id];
          if (marker) {
            applyStatus(marker, entry);
          }
        });
      })
      .catch(function (error) {
        console.warn("presence map:", error);
      });
  }

  /* --- zoom & pan --------------------------------------------------- */

  var view = { x: 0, y: 0, w: MAP_WIDTH, h: MAP_HEIGHT };

  function clampView() {
    view.w = Math.min(MAP_WIDTH, Math.max(MIN_VIEW_WIDTH, view.w));
    view.h = view.w * (MAP_HEIGHT / MAP_WIDTH);
    view.x = Math.min(MAP_WIDTH - view.w, Math.max(0, view.x));
    view.y = Math.min(MAP_HEIGHT - view.h, Math.max(0, view.y));
  }

  function applyView() {
    svg.setAttribute(
      "viewBox",
      view.x.toFixed(2) +
        " " +
        view.y.toFixed(2) +
        " " +
        view.w.toFixed(2) +
        " " +
        view.h.toFixed(2)
    );
    markers.forEach(function (marker) {
      marker.anchor.style.left =
        (((marker.x - view.x) / view.w) * 100).toFixed(3) + "%";
      marker.anchor.style.top =
        (((marker.y - view.y) / view.h) * 100).toFixed(3) + "%";
    });
    var zoomed = view.w < MAP_WIDTH - 0.01;
    wrap.classList.toggle("map-zoomed", zoomed);
    zoomInButton.disabled = view.w <= MIN_VIEW_WIDTH + 0.01;
    zoomOutButton.disabled = !zoomed;
    zoomResetButton.disabled = !zoomed;
  }

  /* Zoom by `factor`, keeping the map point (userX, userY) fixed. */
  function zoomAt(factor, userX, userY) {
    var newWidth = Math.min(
      MAP_WIDTH,
      Math.max(MIN_VIEW_WIDTH, view.w / factor)
    );
    var scale = newWidth / view.w;
    view.x = userX - (userX - view.x) * scale;
    view.y = userY - (userY - view.y) * scale;
    view.w = newWidth;
    clampView();
    applyView();
  }

  /* The map point currently under a mouse/pointer event. */
  function eventUserPoint(event) {
    var rect = svg.getBoundingClientRect();
    return {
      x: view.x + ((event.clientX - rect.left) / rect.width) * view.w,
      y: view.y + ((event.clientY - rect.top) / rect.height) * view.h,
    };
  }

  zoomInButton.addEventListener("click", function () {
    zoomAt(1.5, view.x + view.w / 2, view.y + view.h / 2);
  });
  zoomOutButton.addEventListener("click", function () {
    zoomAt(1 / 1.5, view.x + view.w / 2, view.y + view.h / 2);
  });
  zoomResetButton.addEventListener("click", function () {
    view = { x: 0, y: 0, w: MAP_WIDTH, h: MAP_HEIGHT };
    applyView();
  });

  wrap.addEventListener(
    "wheel",
    function (event) {
      event.preventDefault();
      var point = eventUserPoint(event);
      zoomAt(Math.pow(2, -event.deltaY / 300), point.x, point.y);
    },
    { passive: false }
  );

  wrap.addEventListener("dblclick", function (event) {
    event.preventDefault();
    var point = eventUserPoint(event);
    zoomAt(2, point.x, point.y);
  });

  var drag = null;

  wrap.addEventListener("pointerdown", function (event) {
    if (
      view.w >= MAP_WIDTH ||
      event.button !== 0 ||
      event.target.closest(".map-controls")
    ) {
      return;
    }
    drag = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startViewX: view.x,
      startViewY: view.y,
      moved: false,
    };
    /* Capturing here would retarget the pointerup — and therefore the
       click — to the wrapper, breaking plain clicks on markers and the
       zoom buttons. Capture only once real movement starts (below). */
  });

  wrap.addEventListener("pointermove", function (event) {
    if (!drag || event.pointerId !== drag.pointerId) {
      return;
    }
    var rect = svg.getBoundingClientRect();
    var deltaX = event.clientX - drag.startClientX;
    var deltaY = event.clientY - drag.startClientY;
    if (!drag.moved && Math.abs(deltaX) + Math.abs(deltaY) > 3) {
      drag.moved = true;
      wrap.classList.add("map-dragging");
      wrap.setPointerCapture(event.pointerId);
    }
    if (!drag.moved) {
      return;
    }
    view.x = drag.startViewX - (deltaX / rect.width) * view.w;
    view.y = drag.startViewY - (deltaY / rect.height) * view.h;
    clampView();
    applyView();
  });

  function endDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) {
      return;
    }
    wrap.classList.remove("map-dragging");
    var moved = drag.moved;
    drag = null;
    if (moved) {
      /* Swallow the click that follows a drag released over a marker,
         so panning never navigates to a location page. The click (if
         any) fires in the same input sequence as the pointerup, so
         dropping the guard on the next tick cannot eat a later click. */
      var swallowClick = function (clickEvent) {
        clickEvent.preventDefault();
        clickEvent.stopPropagation();
      };
      wrap.addEventListener("click", swallowClick, { capture: true });
      setTimeout(function () {
        wrap.removeEventListener("click", swallowClick, { capture: true });
      }, 0);
    }
  }

  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);

  refreshTimes();
  refreshShadow();
  applyView();
  setInterval(refreshTimes, 30000);
  setInterval(refreshShadow, 60000);
  setInterval(refreshStatuses, 60000);
})();
