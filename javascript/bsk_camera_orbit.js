(() => {
    const radians = Math.PI / 180;
    const axes = ["azimuth", "elevation", "distance", "roll"];
    const bounds = {azimuth: 180, elevation: 90, distance: 100, roll: 180};
    const rest = {azimuth: 0, elevation: 0, distance: 0, roll: 0};
    const mounted = new WeakSet();

    function adjust(state, axis, delta) {
        let value = Math.round(state[axis] + delta);
        const bound = bounds[axis];
        if (axis === "azimuth" || axis === "roll") {
            value = ((value + 180) % 360 + 360) % 360 - 180;
        }
        return {...state, [axis]: Math.max(-bound, Math.min(bound, value))};
    }

    function describe({azimuth, elevation, distance, roll}) {
        const side = azimuth > 0 ? "왼쪽" : "오른쪽";
        const facing = ["정면", "정면 " + side, side, "후면 " + side, "후면"];
        const direction = facing[Math.round(Math.abs(azimuth) / 45)];
        const height = elevation === 0 ? "평시점" : (elevation > 0 ? "위 " : "아래 ") + Math.abs(elevation) + "°";
        const framing = distance < -70 ? "원경" : distance < -20 ? "전신" :
            distance <= 20 ? "중경" : distance <= 70 ? "근접" : "극근접";
        return direction + " " + azimuth + "° · " + height + " · " + framing + " · 롤 " + roll + "°";
    }

    function line(ctx, a, b) {
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
    }

    function orbit(ctx, pointAt) {
        for (const near of [false, true]) {
            ctx.beginPath();
            ctx.strokeStyle = near ? "#426479" : "#304959";
            ctx.setLineDash(near ? [] : [3, 5]);
            let drawing = false;
            for (let step = 0; step <= 120; step++) {
                const point = pointAt(step * Math.PI / 60);
                if ((point.depth >= 0) !== near) {
                    drawing = false;
                    continue;
                }
                if (drawing) ctx.lineTo(point.x, point.y);
                else ctx.moveTo(point.x, point.y);
                drawing = true;
            }
            ctx.stroke();
        }
        ctx.setLineDash([]);
    }

    function drawSubject(ctx, center) {
        ctx.save();
        ctx.translate(center.x, center.y);
        ctx.fillStyle = "#183847";
        ctx.strokeStyle = "#74ccd9";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(0, 0, 21, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "#a2e5ed";
        ctx.beginPath();
        ctx.arc(0, -6, 4.4, 0, 2 * Math.PI);
        ctx.fill();
        ctx.beginPath();
        ctx.roundRect(-9, 1, 18, 8, [5, 5, 2, 2]);
        ctx.fill();
        ctx.restore();
    }

    function drawCamera(ctx, point, center, roll) {
        ctx.save();
        ctx.translate(point.x, point.y);
        ctx.rotate(Math.atan2(center.y - point.y, center.x - point.x));
        ctx.fillStyle = "#ffc48f";
        ctx.strokeStyle = "#111e2a";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.roundRect(-13, -8, 20, 16, 4);
        ctx.moveTo(6, -4);
        ctx.lineTo(13, -7);
        ctx.lineTo(13, 7);
        ctx.lineTo(6, 4);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.restore();
        if (roll !== 0) {
            const angle = (roll - 90) * radians;
            ctx.fillStyle = "#ffc48f";
            ctx.beginPath();
            ctx.arc(point.x + 19 * Math.cos(angle), point.y + 19 * Math.sin(angle), 2.5, 0, 2 * Math.PI);
            ctx.fill();
        }
    }

    function render(canvas, state) {
        const {width, height} = canvas.getBoundingClientRect();
        if (!width || !height) return;
        const ratio = window.devicePixelRatio || 1;
        const pixelWidth = Math.round(width * ratio);
        const pixelHeight = Math.round(height * ratio);
        if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
            canvas.width = pixelWidth;
            canvas.height = pixelHeight;
        }
        const ctx = canvas.getContext("2d");
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);
        const center = {x: width / 2, y: height / 2};
        const scale = Math.min((width - 52) / 3.1, (height - 40) / 3.1);
        const project = (x, y, z) => ({
            x: center.x + scale * x,
            y: center.y + scale * (0.43 * z - 0.9 * y),
            depth: 0.9 * z + 0.43 * y
        });
        const heading = state.azimuth * radians;
        const pitch = state.elevation * radians;
        const radial = (angle, lift, radius) => project(
            -radius * Math.sin(angle) * Math.cos(lift),
            radius * Math.sin(lift),
            radius * Math.cos(angle) * Math.cos(lift)
        );
        const radius = 1.04 - 0.48 * state.distance / 100;
        const camera = radial(heading, pitch, radius);
        const separation = Math.hypot(camera.x - center.x, camera.y - center.y);
        if (separation < 32) {
            // Offset the marker just enough to keep both symbols visible at overlapping projections.
            const bearing = separation < 0.1 ? (camera.depth >= 0 ? 1 : -1) * Math.PI / 2 :
                Math.atan2(camera.y - center.y, camera.x - center.x);
            camera.x = center.x + 32 * Math.cos(bearing);
            camera.y = center.y + 32 * Math.sin(bearing);
        }

        ctx.lineWidth = 1;
        orbit(ctx, angle => radial(angle, 0, 1.43));
        orbit(ctx, angle => radial(heading, angle, 1.43));

        ctx.font = "600 12px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        for (const [angle, label, offsetY] of [
            [0, "정면", 19], [Math.PI, "후면", -19],
            [Math.PI / 2, "왼쪽", 18], [-Math.PI / 2, "오른쪽", 18]
        ]) {
            const point = radial(angle, 0, 1.43);
            ctx.fillStyle = "#84bdce";
            ctx.beginPath();
            ctx.arc(point.x, point.y, 2.5, 0, 2 * Math.PI);
            ctx.fill();
            ctx.fillStyle = "#b6c9da";
            ctx.fillText(label, point.x, point.y + offsetY);
        }

        ctx.strokeStyle = "#d5a778";
        ctx.lineWidth = 1.4;
        ctx.setLineDash([4, 5]);
        line(ctx, camera, center);
        ctx.setLineDash([]);
        if (camera.depth < 0) drawCamera(ctx, camera, center, state.roll);
        drawSubject(ctx, center);
        if (camera.depth >= 0) drawCamera(ctx, camera, center, state.roll);
    }

    function mount() {
        const app = gradioApp();
        const view = app.querySelector("[data-bsk-orbit]");
        if (!view || mounted.has(view)) return;
        const inputs = Object.fromEntries(axes.map(axis => [
            axis, app.querySelector("#bsk-camera-" + axis + " input[type='range']")
        ]));
        if (Object.values(inputs).some(input => !input)) return;
        mounted.add(view);

        const canvas = view.querySelector("canvas");
        const position = view.querySelector("[data-bsk-position]");
        const read = () => Object.fromEntries(axes.map(axis => [axis, inputs[axis].valueAsNumber]));
        let draft = read();
        let drag = null;
        let frame;

        function draw() {
            render(canvas, draft);
            const label = describe(draft);
            if (position.textContent !== label) position.textContent = label;
        }

        function sync() {
            if (drag) return;
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                draft = read();
                draw();
            });
        }

        function commit() {
            for (const axis of axes) {
                const input = inputs[axis];
                if (input.valueAsNumber === draft[axis]) continue;
                input.value = draft[axis];
                input.dispatchEvent(new Event("input", {bubbles: true}));
            }
            draw();
        }

        for (const axis of axes) {
            const slider = inputs[axis].closest("#bsk-camera-" + axis);
            slider.addEventListener("input", sync);
            slider.addEventListener("change", sync);
        }
        const resize = new ResizeObserver(draw);
        resize.observe(canvas);

        canvas.addEventListener("pointerdown", event => {
            if (event.button !== 0 || drag) return;
            canvas.focus({preventScroll: true});
            canvas.setPointerCapture(event.pointerId);
            draft = read();
            drag = {pointer: event.pointerId, x: event.clientX, y: event.clientY, state: draft};
        });
        canvas.addEventListener("pointermove", event => {
            if (drag?.pointer !== event.pointerId) return;
            const sensitivity = 300 / canvas.clientWidth;
            draft = adjust(drag.state, "azimuth", (drag.x - event.clientX) * sensitivity);
            draft = adjust(draft, "elevation", (drag.y - event.clientY) * sensitivity);
            draw();
        });
        function finish(event) {
            if (drag?.pointer !== event.pointerId) return;
            drag = null;
            commit();
        }
        canvas.addEventListener("pointerup", finish);
        canvas.addEventListener("lostpointercapture", finish);
        canvas.addEventListener("wheel", event => {
            if (event.ctrlKey || event.deltaY === 0) return;
            event.preventDefault();
            const axis = event.shiftKey ? "roll" : "distance";
            draft = adjust(read(), axis, -Math.sign(event.deltaY) * 5);
            commit();
        }, {passive: false});

        const keys = {
            ArrowLeft: ["azimuth", 5], ArrowRight: ["azimuth", -5],
            ArrowUp: ["elevation", 5], ArrowDown: ["elevation", -5],
            "+": ["distance", 5], "=": ["distance", 5], "-": ["distance", -5],
            "[": ["roll", -5], "]": ["roll", 5]
        };
        canvas.addEventListener("keydown", event => {
            if (event.ctrlKey || event.altKey || event.metaKey || !Object.hasOwn(keys, event.key)) return;
            event.preventDefault();
            draft = adjust(read(), ...keys[event.key]);
            commit();
        });
        view.querySelector("[data-bsk-reset]").addEventListener("click", () => {
            draft = rest;
            commit();
        });
        draw();
    }

    onUiLoaded(mount);
    onUiUpdate(mount);
})();
