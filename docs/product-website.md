# Product website

The public product showcase is served at `/` for visitors and at `/product` for everyone, including signed-in members. Signed-in requests to `/` still open the dashboard.

Run `.venv/bin/python run_cloud.py`, then open <http://127.0.0.1:8766/product>.

## Design and assets

The page alternates white, soft grey and graphite sections, with blue reserved for primary actions. It uses the system font, simple product illustrations and customer-facing language. Model names, analysis rates and engineering diagrams are kept out of the marketing story. The account pages use the same neutral palette.

- `vdm_cloud/templates/home.html`: interactive camera walkthrough, product highlights, incident review with a five-second example, local learning, Eco mode, the product tour, privacy, teams, a fifteen-feature catalogue, onboarding and FAQ.
- `vdm_cloud/static/product.css`: responsive layouts, light/dark sections, subtle reveals, learning and Eco illustrations, and motion preferences.
- `vdm_cloud/static/product.js`: navigation, the quiet/activity/review walkthrough, an interruptible five-second illustration, four learning stages, quiet/movement Eco states, the five-chapter / 25-second tour, seeking, pause, assembly and progressive 3D loading.
- `vdm_cloud/static/product-scene.js`: five floating application cards with locally generated textures and neutral lighting. No camera frames, remote analytics or user data are used.
- `vdm_cloud/static/cloud.css`: matching sign-in, sign-up and account-administration styling.

Illustrated camera views and workspaces are labelled as examples. The tour is real-time 3D, not a prerecorded video or a physical appliance. Its cards represent connecting cameras, noticing possible incidents, keeping context, reviewing footage and learning from the site. All five steps happen in the desktop app. A separate section explains what belongs on the website.

All five product stories begin automatically when their illustrations enter the viewport: the camera walkthrough (21 seconds), the interaction check (12 seconds, including five seconds of evidence and a hold for review), local learning (32 seconds), Eco mode (16 seconds), and the 3D tour (25 seconds). They repeat while visible. Visitors do not need to press Play. Story controls pause or resume individual illustrations, and a floating motion control is always available to stop all motion. OS reduced-motion preferences disable autoplay by default.

A shared animation clock runs at approximately 30 fps and advances only visible, unpaused stories. Offscreen and hidden-document time is not accumulated. A manually paused story stays paused after leaving and re-entering the viewport. Selecting a chapter or state jumps to that part of the story; seeking the 3D timeline or manually assembling its layers pauses that tour. Automatic changes do not repeatedly announce live-region text to screen readers. Mobile navigation supports Escape and returns focus to its toggle. Chapter buttons retain full accessible names when their visible text is condensed to numbers on phones.

The hero pairs oversized neutral typography with an illustrated desktop view on a charcoal surface. Its three states show a quieter scene, movement restoring normal checks, and an example event available for review. A local event card opens over the camera view with a moving before/moment/after timeline. SVG subjects, shadows and tracking outlines move together. These are product illustrations, not live camera feeds or claims that ordinary movement raises an incident. The page uses local vector artwork and CSS motion, with no new media downloads or dependencies.

The interaction illustration includes two people and a drawing progress ring. Learning cards separate, tilt, check and settle together as their captions advance. Eco alternates between a quiet rhythm and an animated activity trace. The 3D tour starts assembled, opens into five layers, brings each active layer forward and closes the stack at the end of the cycle. These demonstrations explain features without presenting internal models or implementation details to customers.

Only one 3D scene is loaded, near the viewport. Pixel density is capped at 1.5 and rendering at approximately 30 fps. Offscreen, hidden and paused scenes stop continuous rendering. A lost or unavailable WebGL context falls back to the labelled layer illustration; text and tour controls remain available. Text content and account links remain available without JavaScript.

All runtime assets are served from the same origin. The existing Content Security Policy requires no inline-script, inline-style, eval or CDN exceptions. Packaged wheels include the template, styles, scripts, icon, renderer and licence notices.

## Third-party assets

- Three.js r180, MIT, vendored from the official `mrdoob/three.js` release (`three.module.min.js` and `three.core.min.js`). Licence: `static/vendor/THREE-LICENSE.txt`.
- The previously bundled Space Grotesk font remains available with its SIL Open Font License in `static/fonts/OFL.txt`. The redesigned page uses the system font and does not request it.

## Claims and release state

The desktop app owns camera setup, streams, detection, Eco controls, evidence, incident history and local event learning. Footage, corrected labels and learned files remain on that device. The web workspace manages accounts, groups, licences, activation and administration logs, with numerical device-health summaries.

Learning is operator initiated: review examples, start an update, check it and use only a passing candidate. The marketing page does not promise automatic continuous retraining, human-like intelligence or inevitable improvement with time. Existing detections remain available alongside the local learner. The validation and class-coverage gates in `vmd/learning.py`, and the learned-alert path in `vmd/engine.py`, remain the source of truth; the redesign does not change them.

The Eco illustration shows quiet and moving scenes without presenting illustrative rates as measurements. Eco reduces analysis frequency while retaining periodic checks and restores normal analysis on movement. It remains active during a pending interaction check. Actual savings depend on hardware and scene activity. It is optional, per live camera, and off by default. Uploaded videos use normal processing.

The interaction example explains sustained supporting evidence around the same two people before a possible-fight alert. Movement, closeness and relative depth are described in customer-facing terms. Visitors can interrupt the example to see the progress reset without an alert, or let five seconds complete and see a prompt for human review. It does not run detection, demonstrate accuracy, establish contact or promise that waving and object strikes are reliably distinguished. The FAQ makes these limits explicit. The current Mac release is identified as the 0.2.1 Apple silicon preview.

The catalogue includes possible incident and visible-object alerts, a relative sense of space, camera compatibility and reconnect attempts, event context and history, review and corrections, local learning, Eco mode, device health, retention, group access and local operator guides. No guaranteed detection, exact distance or measured performance claims are made. The Mac download remains an Apple silicon preview and requires sign-in.

Public hosting, SMTP and a signed/notarized production Mac release remain deployment work; see `group-workspaces.md`. Updating this website does not publish the service externally.

## Verification

The automatic-motion update passed JavaScriptCore syntax checks for both scripts and Flask checks for rendered templates, element references, anchors, static assets and the strict CSP. A JavaScriptCore lifecycle harness exercised the actual page script with a simulated clock and visibility events: autoplay, looping, offscreen suspension, resuming, persistent manual pauses, the global control, hidden-document behavior, reduced-motion defaults, interrupted evidence and page teardown all passed.

WebKit visual verification covered automatic hero and 3D chapter progression, the event card and timeline, automatic learning stages, the new interaction illustration, the global pause control and responsive Eco playback. The event card and automatically unfolding 3D scene were checked down to 320 pixels. These checks are for the website presentation and do not evaluate the detection model.

The September 23 refresh passed 26 account, group and licensing tests. Separate Flask smoke checks covered anonymous and member landing pages, account routes, both trial-copy variants, unique anchors, JavaScript element references, static assets and the existing strict CSP. The updated script passed JavaScriptCore syntax validation.

Visual checks in an isolated WebKit window covered the desktop hero, its manual activity state and finite autoplay, completed and interrupted five-second examples, local-learning and Eco controls, the updated 3D chapter and layer assembly, phone navigation with Escape, and layouts down to 320 pixels. Animations and example timers stop offscreen or when the document is hidden. Public hosting and cross-browser release QA remain separate from this local preview.

The neutral redesign was checked in an isolated WebKit preview at desktop, phone and 320-pixel widths. Checks covered the workspace illustration, local-learning controls, both Eco states, 3D rendering and assembly, tour chapters, playback completion, seeking, mobile navigation and Escape, FAQ expansion, motion pause and the sign-up handoff.

Flask smoke checks covered anonymous and signed-in product pages, the dashboard redirect, account and download links, unique section anchors, static assets and MIME types, the strict CSP, configurable trial copy and the no-trial variant. Both JavaScript files passed JavaScriptCore syntax checks, and their referenced element IDs were checked against the rendered template. A fresh wheel build was checked against the current template and all eleven static assets byte-for-byte.

The earlier account integration passed 19 account/licensing/group tests; the earlier feature revision passed 13 Eco/learning/temporal tests. The visual redesign changes templates and static assets only. Other browser/GPU combinations remain part of deployment QA.
