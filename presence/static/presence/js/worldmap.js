/*
 * World map page: places one marker per location (positions computed
 * server-side from the astral city database) and overlays the day/night
 * terminator, both on the 1000x500 equirectangular map in map.html.
 *
 * The subsolar point comes from the standard NOAA low-accuracy solar
 * position formulas (a fraction of a degree of error — invisible at this
 * map scale). No dependencies.
 */
(function () {
  "use strict";

  var RAD = Math.PI / 180;
  var MAP_WIDTH = 1000;
  var MAP_HEIGHT = 500;

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
  var shadow = document.getElementById("night-shadow");
  var utcCaption = document.getElementById("map-utc");
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

  var markers = locations.map(function (location) {
    var anchor = document.createElement("a");
    anchor.className = "map-marker";
    anchor.href = location.url;
    anchor.style.left =
      (((location.longitude + 180) / 360) * 100).toFixed(2) + "%";
    anchor.style.top =
      (((90 - location.latitude) / 180) * 100).toFixed(2) + "%";
    anchor.title = location.city + " · " + location.timezone;

    var label = document.createElement("span");
    label.className = "map-marker-label";
    var dot = document.createElement("span");
    dot.className = "map-marker-dot";
    anchor.appendChild(label);
    anchor.appendChild(dot);
    wrap.appendChild(anchor);

    return {
      name: location.name,
      formatter: timeFormatter(location.timezone),
      label: label,
    };
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

  refreshTimes();
  refreshShadow();
  setInterval(refreshTimes, 30000);
  setInterval(refreshShadow, 60000);
})();
