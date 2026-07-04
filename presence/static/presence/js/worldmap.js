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

  /* One tooltip line per presence, e.g. "Lamp: off · outside window ·
     on at Sat 19:12" — answering "why is that light off?" directly from
     the map. Times arrive from the server already rendered in the
     location's timezone. */
  function presenceLine(detail) {
    if (detail.state === "disabled") {
      return detail.name + ": disabled";
    }
    var line = detail.name + ": " + detail.state;
    if (detail.state === "off" && !detail.in_window) {
      line += " · outside window";
    }
    if (detail.next_transition) {
      line +=
        " · " +
        (detail.state === "on" ? "off at " : "on at ") +
        detail.next_transition;
    }
    return line;
  }

  function applyStatus(marker, entry) {
    marker.dot.className = "map-marker-dot status-" + entry.status;
    var summary = presenceSummary(entry);
    /* A position-only location (issue #54) has no city to lead with. */
    var lines = [
      (marker.city ? marker.city + " · " : "") +
        marker.timezone +
        (summary ? " — " + summary : " — no presences"),
    ];
    entry.presences.forEach(function (detail) {
      lines.push(presenceLine(detail));
    });
    marker.anchor.title = lines.join("\n");
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
    declutterLabels(); /* the new time text can change label widths */
  }

  /*
   * Labels of nearby locations (e.g. London and Copenhagen) draw on top
   * of each other, and a label near the map edge is clipped by the
   * wrapper. After every reposition: clamp each visible label inside the
   * wrapper horizontally, then lift colliding labels vertically so all
   * stay readable. Southern markers are placed first and keep their
   * natural spot; a colliding label further north stacks above it.
   */
  function rectanglesOverlap(a, b) {
    return (
      a.left < b.right &&
      b.left < a.right &&
      a.top < b.bottom &&
      b.top < a.bottom
    );
  }

  function declutterLabels() {
    var wrapRect = wrap.getBoundingClientRect();
    markers.forEach(function (marker) {
      marker.label.style.transform = "";
    });
    var visible = markers.filter(function (marker) {
      /* Markers panned out of the view keep their default (clipped)
         labels; clamping those back inside would be wrong. Unpositioned
         markers (before the first applyView) parse as NaN and are
         skipped too. */
      var xPercent = parseFloat(marker.anchor.style.left);
      var yPercent = parseFloat(marker.anchor.style.top);
      return xPercent >= 0 && xPercent <= 100 && yPercent >= 0 && yPercent <= 100;
    });
    visible.sort(function (a, b) {
      return b.y - a.y;
    });
    var placed = [];
    visible.forEach(function (marker) {
      var measured = marker.label.getBoundingClientRect();
      var shiftX = 0;
      if (measured.left < wrapRect.left) {
        shiftX = wrapRect.left - measured.left;
      } else if (measured.right > wrapRect.right) {
        shiftX = wrapRect.right - measured.right;
      }
      var rect = {
        left: measured.left + shiftX,
        right: measured.right + shiftX,
        top: measured.top,
        bottom: measured.bottom,
      };
      /* Lift until clear of every already-placed label. Bounded, since
         a pathological pile-up must not spin the pointermove handler. */
      var guard = 0;
      var collided = true;
      while (collided && guard < 20) {
        collided = false;
        guard += 1;
        for (var i = 0; i < placed.length; i += 1) {
          if (rectanglesOverlap(rect, placed[i])) {
            var raiseBy = rect.bottom - placed[i].top + 2;
            rect.top -= raiseBy;
            rect.bottom -= raiseBy;
            collided = true;
          }
        }
      }
      var lift = measured.top - rect.top;
      if (shiftX || lift) {
        /* Repeat the stylesheet's centering translateX(-50%): setting
           style.transform replaces it, not composes with it. */
        marker.label.style.transform =
          "translateX(-50%) translate(" +
          shiftX.toFixed(1) +
          "px, -" +
          lift.toFixed(1) +
          "px)";
      }
      placed.push(rect);
    });
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
    declutterLabels();
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

  /* Swallow the click that follows a drag or pinch released over a
     marker, so the gesture never navigates to a location page. The
     click (if any) fires in the same input sequence as the pointerup,
     so dropping the guard on the next tick cannot eat a later click. */
  function swallowNextClick() {
    var swallowClick = function (clickEvent) {
      clickEvent.preventDefault();
      clickEvent.stopPropagation();
    };
    wrap.addEventListener("click", swallowClick, { capture: true });
    setTimeout(function () {
      wrap.removeEventListener("click", swallowClick, { capture: true });
    }, 0);
  }

  /* --- pinch zoom (touch) -------------------------------------------- */

  var touchPoints = {}; /* pointerId -> latest client coordinates */
  var pinch = null;

  function touchIds() {
    return Object.keys(touchPoints);
  }

  function pinchGeometry() {
    var ids = touchIds();
    var a = touchPoints[ids[0]];
    var b = touchPoints[ids[1]];
    return {
      /* || 1: two stacked fingers must not divide the scale by zero */
      distance: Math.hypot(a.x - b.x, a.y - b.y) || 1,
      midX: (a.x + b.x) / 2,
      midY: (a.y + b.y) / 2,
    };
  }

  function startPinch() {
    var geometry = pinchGeometry();
    var rect = svg.getBoundingClientRect();
    pinch = {
      startDistance: geometry.distance,
      startWidth: view.w,
      /* The map point under the fingers' midpoint stays under it as
         both the zoom and the midpoint move — pinch pans too. */
      anchorX: view.x + ((geometry.midX - rect.left) / rect.width) * view.w,
      anchorY: view.y + ((geometry.midY - rect.top) / rect.height) * view.h,
    };
    drag = null;
    wrap.classList.remove("map-dragging");
    touchIds().forEach(function (id) {
      /* Capture keeps fast-moving fingers delivering events after they
         leave the wrapper. It can only fail for a pointer that is no
         longer active — then there is nothing to capture and the pinch
         still works from bubbled events, so don't abort the gesture. */
      try {
        wrap.setPointerCapture(Number(id));
      } catch (error) {
        console.warn("presence map:", error);
      }
    });
  }

  function movePinch() {
    var geometry = pinchGeometry();
    var rect = svg.getBoundingClientRect();
    view.w = Math.min(
      MAP_WIDTH,
      Math.max(
        MIN_VIEW_WIDTH,
        pinch.startWidth * (pinch.startDistance / geometry.distance)
      )
    );
    view.h = view.w * (MAP_HEIGHT / MAP_WIDTH);
    view.x = pinch.anchorX - ((geometry.midX - rect.left) / rect.width) * view.w;
    view.y = pinch.anchorY - ((geometry.midY - rect.top) / rect.height) * view.h;
    clampView();
    applyView();
  }

  /* --- drag pan (mouse or single touch) ------------------------------ */

  wrap.addEventListener("pointerdown", function (event) {
    if (event.target.closest(".map-controls")) {
      return;
    }
    if (event.pointerType === "touch") {
      touchPoints[event.pointerId] = { x: event.clientX, y: event.clientY };
      if (touchIds().length === 2) {
        startPinch();
        return;
      }
      if (touchIds().length > 2) {
        return; /* extra fingers are ignored, not part of the pinch */
      }
    }
    if (view.w >= MAP_WIDTH || event.button !== 0 || pinch) {
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
    if (event.pointerType === "touch" && touchPoints[event.pointerId]) {
      touchPoints[event.pointerId] = { x: event.clientX, y: event.clientY };
      if (pinch) {
        if (touchIds().length >= 2) {
          movePinch();
        }
        return;
      }
    }
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
    if (event.pointerType === "touch") {
      delete touchPoints[event.pointerId];
      if (pinch && touchIds().length < 2) {
        /* Pinch over: don't fall back into a drag with the leftover
           finger (its start point is unknown); a fresh touch pans. */
        pinch = null;
        swallowNextClick();
        return;
      }
    }
    if (!drag || event.pointerId !== drag.pointerId) {
      return;
    }
    wrap.classList.remove("map-dragging");
    var moved = drag.moved;
    drag = null;
    if (moved) {
      swallowNextClick();
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
