frappe.ui.form.on("TS Token", {
	refresh(frm) {
		// Post-dated entry — ONLY on brand-new tokens being created (Lesson 166).
		// Previously triggered on every draft view, polluting 197 tokens on prod
		// and silently unlocking entry_date/entry_time on already-saved drafts.
		if (frm.is_new()) {
			if (frm._pd_access && frm._pd_access.enabled) {
				_tkn_pd_apply(frm, frm._pd_access);
			}
			if (!frm._pd_fetched) {
				frm._pd_fetched = true;
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.check_post_dated_access",
					args: { doctype: "TS Token", token_number: frm.doc.name || "" },
					async: true,
					callback(r) {
						if (r.message && r.message.enabled) {
							frm._pd_access = r.message;
							_tkn_pd_apply(frm, r.message);
						}
					}
				});
			}
		} else {
			// Saved draft or submitted — remove any stale banner
			$(frm.wrapper).find(".pd-banner").remove();
		}

		let is_gate_pass = frm.doc.entry_type === "Gate Pass";
		let is_admin_reception = frappe.user.has_role("Admin Reception") && !frappe.user.has_role("G1 Security");

		// Force show driver_name for Material tokens
		// Frappe's depends_on section evaluation sets disp_status="None" on fields
		// that were previously hidden, even after unhiding. Must force after Frappe completes.
		if (!is_gate_pass) {
			setTimeout(function() {
				if (frm.fields_dict.driver_name) {
					frm.fields_dict.driver_name.disp_status = "Write";
					frm.fields_dict.driver_name.refresh();
					$(frm.fields_dict.driver_name.wrapper).removeClass("hide-control").show();
				}
			}, 200);
		}

		// Hide Connections sidebar for Gate Pass (material-only links)
		if (is_gate_pass && frm.dashboard && frm.dashboard.wrapper) {
			frm.dashboard.wrapper.find(".form-links, .form-heatmap, .form-graph").hide();
		}

		// Admin Reception: force Gate Pass mode and hide material fields
		if (is_admin_reception) {
			if (frm.is_new()) {
				frm.set_value("entry_type", "Gate Pass");
			}
			frm.set_df_property("entry_type", "read_only", 1);
			// Hide material-only sections
			frm.set_df_property("vehicle_section", "hidden", 1);
			frm.set_df_property("timestamps_section", "hidden", 1);
			frm.set_df_property("turnaround_section", "hidden", 1);
		}

		// Apply route_options defaults (e.g., from workspace shortcut)
		if (frm.is_new() && frappe.route_options && frappe.route_options.entry_type) {
			frm.set_value("entry_type", frappe.route_options.entry_type);
			delete frappe.route_options.entry_type;
		}

		// Filter visitor Link to show enabled visitors
		frm.set_query("visitor", function () {
			return { filters: { enabled: 1 } };
		});

		// Filter driver to hide blacklisted (driver is filled at G2 via Gate Entry)
		frm.set_query("driver", function () {
			return { filters: { is_blacklisted: 0 } };
		});

		// Purpose + Stock Direction are declared at G2 — when the Gate Entry is
		// submitted it sets both on the token (purpose = material_flow, stock_direction).
		// Reception (G1) no longer TYPES them, but they stay VISIBLE (read-only) so
		// everyone can see the load's purpose; they fill in once the gate entry is
		// submitted at G2. Both are reqd=0, so read-only never blocks token creation.
		frm.set_df_property("purpose", "read_only", 1);
		frm.set_df_property("stock_direction", "read_only", 1);

		// Hide barcode and token_number on new unsaved form
		if (frm.is_new()) {
			frm.set_df_property("barcode", "hidden", 1);
			frm.set_df_property("token_number", "hidden", 1);
		} else {
			frm.set_df_property("barcode", "hidden", 0);
			frm.set_df_property("token_number", "hidden", 0);

			// Lock entry_type after save (cannot change Material <-> Gate Pass)
			frm.set_df_property("entry_type", "read_only", 1);

			// Lock common fields after save
			if (is_gate_pass) {
				// Lock gate pass fields after save
				let gp_lock_fields = [
					"visitor_name", "visitor_company", "contact_number",
					"id_proof_type", "id_proof_number", "visit_purpose",
					"destination", "number_of_visitors"
				];
				gp_lock_fields.forEach(f => frm.set_df_property(f, "read_only", 1));
			} else {
				// Lock vehicle number after save (G1 fills it, no changes after)
				frm.set_df_property("vehicle_number", "read_only", 1);
			}

			// === PRINT BUTTONS ===
			let _open_print = function (format) {
				window.open(
					"/printview?doctype=" + encodeURIComponent("TS Token")
					+ "&name=" + encodeURIComponent(frm.doc.name)
					+ "&format=" + encodeURIComponent(format)
				);
			};
			if (is_gate_pass) {
				frm.add_custom_button(__("Print Gate Pass"), function () {
					_open_print("TS Gate Pass");
				}).addClass("btn-primary-dark");
			} else {
				let is_g2_only = frappe.user.has_role("G2 Gate Operator")
					&& !frappe.user.has_role("IT Head")
					&& !frappe.user.has_role("System Manager");
				if (is_g2_only) {
					frappe.db.get_single_value("TS Settings", "g2_print_mode").then(mode => {
						if (mode === "Detailed + Slip") {
							frm.add_custom_button(__("Detailed"), () => _open_print("TS Token Print"), __("Print"));
						}
						frm.add_custom_button(__("Slip"), () => _open_print("TS Token Slip"), __("Print"));
					});
				} else {
					frm.add_custom_button(__("Detailed"), () => _open_print("TS Token Print"), __("Print"));
					frm.add_custom_button(__("Slip"), () => _open_print("TS Token Slip"), __("Print"));
				}
			}

			// === MATERIAL TOKEN BUTTONS ===
			if (!is_gate_pass) {
				// Create GRN button - restricted roles (Stock IN only)
				let grn_roles = ["Accounts Manager", "Accounts User", "Stores User", "IT Head", "System Manager"];
				let can_create_grn = grn_roles.some(r => frappe.user.has_role(r));
				if (frm.doc.status === "Tare Weighed" && !frm.doc.purchase_receipt && can_create_grn && frm.doc.stock_direction !== "Stock OUT") {
					frm.add_custom_button(__("Create GRN"), function () {
						frappe.confirm(
							__("Create Purchase Receipt (GRN) for this token?<br><br>This will generate a Purchase Receipt against the linked Purchase Order with the net weight from the Weighbridge."),
							function () {
								frm.call("create_grn").then((r) => {
									frm.reload_doc();
									if (r.message && r.message.purchase_receipt) {
										frappe.show_alert({
											message: __("GRN {0} created successfully", [r.message.purchase_receipt]),
											indicator: "green"
										});
									}
								});
							}
						);
					}).addClass("btn-primary");
				}

				// View GRN button
				if (frm.doc.purchase_receipt) {
					frm.add_custom_button(__("View GRN"), function () {
						frappe.set_route("Form", "Purchase Receipt", frm.doc.purchase_receipt);
					}, __("Actions"));
				}

				// View Delivery Note button (Stock OUT)
				if (frm.doc.delivery_note) {
					frm.add_custom_button(__("View Delivery Note"), function () {
						frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note);
					}, __("Actions"));
				}

				// Mark Exit (legacy single-step exit).
				// v2.9.8.20: NEVER show for Raw Material — that path is now ALWAYS the
				// two-step G2 Exit + G1 Final Exit flow (rendered below, regardless of
				// ts_two_pass_gates_enabled). Mark Exit still shows for Stock OUT and
				// for Material tokens that aren't Raw Material (per legacy single-pass
				// fallback when the two-pass entry flow is disabled).
				let show_mark_exit = false;
				if (frm.doc.status && !["Exited", "Campus Exited", "Plant Exited"].includes(frm.doc.status) && !frm._ts_two_pass_flag) {
					if (frm.doc.stock_direction === "Stock OUT") {
						show_mark_exit = ["Gross Recorded", "Dispatch Ready"].includes(frm.doc.status);
					} else if (frm.doc.purpose === "Raw Material") {
						// v2.9.8.20: Raw Material always uses G2/G1 buttons below.
						show_mark_exit = false;
					} else {
						show_mark_exit = true;
					}
				}

				if (show_mark_exit && !frappe.user.has_role("Weighbridge Operator")) {
					frm.add_custom_button(__("Mark Exit"), function () {
						frappe.confirm(
							__("Are you sure you want to mark this vehicle as exited?"),
							function () {
								frm.call("mark_exit").then(() => {
									frm.reload_doc();
									frappe.show_alert({
										message: __("Vehicle marked as exited"),
										indicator: "green"
									});
								});
							}
						);
					}, __("Actions"));
				}

				// =====================================================================
				// v2.8.3 Two-Pass Gate Flow buttons (Material tokens only).
				// v2.9.8.20: EXIT buttons (B2 + B3) now render UNCONDITIONALLY for
				// Material — decoupled from the entry-side flag. Entry-side B1
				// (Record G2 Entry) still gated on ts_two_pass_gates_enabled because
				// that intermediate G1 Entered → G2 Entered status only exists when
				// the entry flow is two-pass.
				//
				// Gates (per button):
				//   - B1 (Record G2 Entry) — flag ON + saved + G2 role + status=G1 Entered
				//   - B2 (Record G2 Exit)  — saved + G2 role + status in (Tare Weighed, GRN Created)
				//   - B3 (Record G1 Final Exit) — saved + G1 role + status=Plant Exited
				// =====================================================================
				const _render_two_pass_buttons = () => {
					if (frappe.session.user === "Guest") return;

					const has_g2 = frappe.user.has_role("G2 Gate Operator")
						|| frappe.user.has_role("IT Head")
						|| frappe.user.has_role("System Manager");
					const has_g1 = frappe.user.has_role("G1 Security")
						|| frappe.user.has_role("IT Head")
						|| frappe.user.has_role("System Manager")
						|| frappe.user.has_role("Admin Reception");
					// v2.18.0 — Stores roles may approve a Non-Raw-Material vehicle for exit.
					const has_stores = frappe.user.has_role("Stores User")
						|| frappe.user.has_role("Stores Manager")
						|| frappe.user.has_role("IT Head")
						|| frappe.user.has_role("System Manager");
					const _is_non_rm = (frm.doc.purpose === "Non-Raw Material");
					const _already_exited = ["Plant Exited", "Campus Exited", "Exited"].includes(frm.doc.status);

					// v2.18.0 — "Exit Approved" (Stores). Authorises a Non-RM vehicle to
					// leave with no weighbridge / GRN / two-pass; unlocks B2 + B3 below.
					if (has_stores && _is_non_rm && !frm.doc.non_rm_exit_approved && !_already_exited) {
						frm.add_custom_button(__("Exit Approved"), function () {
							frappe.confirm(
								__("Approve this Non-Raw-Material vehicle to exit? G2 and G1 will then be able to record exit."),
								function () {
									frappe.call({
										method: "trustbit_ethanol.ts_gate_entry.doctype.ts_token.ts_token.approve_non_rm_exit",
										args: { token_name: frm.doc.name },
										callback: () => {
											frm.reload_doc();
											frappe.show_alert({ message: __("Exit approved — G2 can now record exit"), indicator: "green" });
										}
									});
								}
							);
						}, __("Gate Actions")).addClass("btn-primary");
					}

					// B1 — Record G2 Entry (status=G1 Entered)
					if (has_g2 && frm.doc.status === "G1 Entered") {
						frm.add_custom_button(__("Record G2 Entry"), function () {
							frappe.confirm(
								__("Confirm vehicle {0} has physically entered the plant at G2?", [frm.doc.vehicle_number || ""]),
								function () {
									frappe.call({
										method: "trustbit_ethanol.ts_gate_entry.doctype.ts_token.ts_token.g2_mat_log_entry",
										args: { token_name: frm.doc.name },
										callback: () => {
											frm.reload_doc();
											frappe.show_alert({ message: __("G2 Entry recorded"), indicator: "green" });
										}
									});
								}
							);
						}, __("Gate Actions")).addClass("btn-primary");
					}

					// B2 — Record G2 Exit (status=Tare Weighed/GRN Created, OR a Stores-approved Non-RM token)
					const _non_rm_exit_ready = _is_non_rm && frm.doc.non_rm_exit_approved && !_already_exited;
					if (has_g2 && (["Tare Weighed", "GRN Created"].includes(frm.doc.status) || _non_rm_exit_ready)) {
						frm.add_custom_button(__("Record G2 Exit"), function () {
							frappe.confirm(
								__("Confirm vehicle has completed unloading and is leaving the plant via G2?"),
								function () {
									frappe.call({
										method: "trustbit_ethanol.ts_gate_entry.doctype.ts_token.ts_token.g2_mat_log_exit",
										args: { token_name: frm.doc.name },
										callback: () => {
											frm.reload_doc();
											frappe.show_alert({ message: __("G2 Exit recorded — status: Plant Exited"), indicator: "yellow" });
										}
									});
								}
							);
						}, __("Gate Actions")).addClass("btn-warning");
					}

					// B3 — Record G1 Final Exit (status=Plant Exited)
					if (has_g1 && frm.doc.status === "Plant Exited") {
						frm.add_custom_button(__("Record G1 Final Exit"), function () {
							frappe.confirm(
								__("Confirm vehicle has physically left the campus via G1?"),
								function () {
									frappe.call({
										method: "trustbit_ethanol.ts_gate_entry.doctype.ts_token.ts_token.g1_final_exit",
										args: { token_name: frm.doc.name },
										callback: () => {
											frm.reload_doc();
											frappe.show_alert({ message: __("Vehicle exited — status: Campus Exited"), indicator: "green" });
										}
									});
								}
							);
						}, __("Gate Actions")).addClass("btn-danger");
					}
				};

				if (!is_gate_pass) {
					// v2.9.8.20: render two-pass buttons UNCONDITIONALLY for Material.
					// Exit-side buttons (B2/B3) are now always available — decoupled
					// from ts_two_pass_gates_enabled. The flag still controls whether
					// B1 (Record G2 Entry) shows, but B1 only ever appears when status
					// is "G1 Entered" which only exists when the entry-side flag is ON.
					// We still fetch the flag so frm._ts_two_pass_flag is populated for
					// any other consumer that needs it (e.g. the Mark Exit gate above).
					const _apply_render = () => _render_two_pass_buttons();
					_apply_render();
					// Backfill flag in cache (best-effort, async) for the Mark Exit
					// rendering path which already runs before this block.
					if (frm._ts_two_pass_flag === undefined) {
						if (frappe.boot && frappe.boot._two_pass_flag !== undefined) {
							frm._ts_two_pass_flag = !!frappe.boot._two_pass_flag;
						} else {
							frappe.call({
								method: "trustbit_ethanol.ts_gate_entry.api.get_two_pass_flag",
								type: "GET",
								callback: (r) => {
									const enabled = !!(r && r.message && r.message.enabled);
									frm._ts_two_pass_flag = enabled;
									if (frappe.boot) frappe.boot._two_pass_flag = enabled;
								},
								error: () => { frm._ts_two_pass_flag = false; },
							});
						}
					}
				}
			}

			// === GATE PASS BUTTONS ===
			if (is_gate_pass) {
				// G2 Log Entry button — G2 Gate Operator when visitor is Inside Campus
				let g2_roles = ["G2 Gate Operator", "IT Head", "System Manager"];
				let is_g2 = g2_roles.some(r => frappe.user.has_role(r));

				if (frm.doc.gate_pass_status === "Inside Campus" && is_g2 && frm.doc.destination) {
					// Check if destination has G2 checkpoint
					frappe.db.get_value("TS Gate Pass Destination", frm.doc.destination, "has_g2_checkpoint", (r) => {
						if (r && r.has_g2_checkpoint) {
							frm.add_custom_button(__("Log G2 Entry"), function () {
								frappe.confirm(
									__("Log visitor entry into the plant at G2?"),
									function () {
										frm.call("g2_log_entry").then(() => {
											frm.reload_doc();
											frappe.show_alert({
												message: __("G2 entry logged"),
												indicator: "green"
											});
										});
									}
								);
							}).addClass("btn-warning");
						}
					});
				}

				// G2 Log Exit button — G2 Gate Operator when visitor is Inside Plant
				if (frm.doc.gate_pass_status === "Inside Plant" && is_g2) {
					frm.add_custom_button(__("Log G2 Exit"), function () {
						frappe.confirm(
							__("Log visitor exit from the plant at G2?"),
							function () {
								frm.call("g2_log_exit").then(() => {
									frm.reload_doc();
									frappe.show_alert({
										message: __("G2 exit logged"),
										indicator: "green"
									});
								});
							}
						);
					}).addClass("btn-warning");
				}

				// Mark Exit for Gate Pass — G1 Security when visitor is Inside Campus (not Inside Plant)
				if (frm.doc.gate_pass_status && frm.doc.gate_pass_status !== "Exited") {
					let g1_roles = ["G1 Security", "Admin Reception", "IT Head", "System Manager"];
					let is_g1 = g1_roles.some(r => frappe.user.has_role(r));

					if (is_g1 && frm.doc.gate_pass_status !== "Inside Plant") {
						frm.add_custom_button(__("Mark Exit"), function () {
							frappe.confirm(
								__("Mark this visitor as exited from the campus?"),
								function () {
									frm.call("mark_exit").then(() => {
										frm.reload_doc();
										frappe.show_alert({
											message: __("Visitor marked as exited"),
											indicator: "green"
										});
									});
								}
							);
						}).addClass("btn-danger");
					}
				}
			}
		}

		// === STATUS INDICATORS ===
		if (is_gate_pass) {
			// Gate Pass status indicators
			if (frm.doc.gate_pass_status === "Inside Campus") {
				frm.page.set_indicator(__("Inside Campus"), "blue");
			} else if (frm.doc.gate_pass_status === "Inside Plant") {
				frm.page.set_indicator(__("Inside Plant"), "orange");
			} else if (frm.doc.gate_pass_status === "Exited") {
				frm.page.set_indicator(__("Exited"), "green");
			} else if (frm.is_new()) {
				frm.page.set_indicator(__("New Gate Pass"), "blue");
			}
		} else {
			// Material token status indicators
			if (frm.doc.status === "Campus Exited" || frm.doc.status === "Exited") {
				frm.page.set_indicator(__("Campus Exited"), "green");
			} else if (frm.doc.status === "Plant Exited") {
				frm.page.set_indicator(__("Plant Exited"), "yellow");
			} else if (frm.doc.purpose === "Non-Raw Material" && frm.doc.non_rm_exit_approved) {
				frm.page.set_indicator(__("Exit Approved"), "yellow");
			} else if (frm.doc.status === "GRN Created") {
				frm.page.set_indicator(__("GRN Created"), "green");
			} else if (frm.doc.status === "Token Generated") {
				frm.page.set_indicator(__("Token Generated"), "blue");
			} else if (frm.doc.status === "G1 Entered" || frm.doc.status === "G2 Entered") {
				frm.page.set_indicator(__(frm.doc.status), "blue");
			} else {
				frm.page.set_indicator(__(frm.doc.status), "orange");
			}
		}

		// Filter destination to show only enabled ones
		frm.set_query("destination", function () {
			return { filters: { enabled: 1 } };
		});
	},

	entry_type(frm) {
		// Clear fields when switching type on new form
		if (frm.is_new()) {
			if (frm.doc.entry_type === "Gate Pass") {
				// Clear material fields (purpose is auto-set from Gate Entry, not on Token form)
				frm.set_value("vehicle_number", "");
				frm.set_value("driver_name", "");
				frm.set_value("driver_mobile", "");
				frm.set_value("driver_license_number", "");
			} else {
				// Clear gate pass fields
				frm.set_value("visitor_name", "");
				frm.set_value("visitor_company", "");
				frm.set_value("contact_number", "");
				frm.set_value("visit_purpose", "");
				frm.set_value("destination", "");
				frm.set_value("host_name", "");
				frm.set_value("host_department", "");
				frm.set_value("purpose_detail", "");
				frm.set_value("id_proof_type", "");
				frm.set_value("id_proof_number", "");
				frm.set_value("expected_duration", "");
				frm.set_value("number_of_visitors", 1);
			}
			frm.refresh_fields();
		}
	},

	destination(frm) {
		// Auto-fill host name from destination default
		if (frm.doc.destination && frm.doc.entry_type === "Gate Pass") {
			frappe.db.get_value("TS Gate Pass Destination", frm.doc.destination, "default_host", (r) => {
				if (r && r.default_host && !frm.doc.host_name) {
					frm.set_value("host_name", r.default_host);
				}
			});
		}
	},

	visitor(frm) {
		// Auto-fill visitor details from TS Visitor master
		if (frm.doc.visitor) {
			frappe.db.get_doc("TS Visitor", frm.doc.visitor).then(v => {
				frm.set_value("visitor_name", v.visitor_name || "");
				frm.set_value("visitor_company", v.visitor_company || "");
				frm.set_value("contact_number", v.contact_number || "");
				frm.set_value("id_proof_type", v.id_proof_type || "");
				frm.set_value("id_proof_number", v.id_proof_number || "");
				if (v.visitor_photo) {
					frm.set_value("visitor_photo", v.visitor_photo);
				}
			});
		} else {
			// Clear fields when visitor is unlinked
			frm.set_value("visitor_name", "");
			frm.set_value("visitor_company", "");
			frm.set_value("contact_number", "");
			frm.set_value("id_proof_type", "");
			frm.set_value("id_proof_number", "");
		}
	}
});

function _tkn_pd_apply(frm, access) {
	try {
		const $target = $(frm.wrapper).find(".form-page");
		if (!$target.find(".pd-banner").length) {
			$target.prepend(`<div class="pd-banner" style="padding:10px 16px;background:#e3f2fd;border:1px solid #bbdefb;border-radius:6px;margin:8px 15px;font-size:13px;color:#1565c0;">
				<strong>&#128197; Post-Dated Entry Enabled</strong> — Dates <strong>${_fmt_pd_date(access.from_date)}</strong> to <strong>${_fmt_pd_date(access.to_date)}</strong> allowed. Active until <strong>${_fmt_pd_date(access.valid_until)}</strong>.
			</div>`);
		}
		["entry_date", "entry_time"].forEach(fn => {
			frm.set_df_property(fn, "read_only", 0);
			if (frm.fields_dict[fn] && frm.fields_dict[fn].$wrapper)
				frm.fields_dict[fn].$wrapper.find("input").css({"border-color": "#2490ef", "background": "#f0f7ff"});
		});
	} catch(e) {}
}

// Format "2026-05-14" → "14 May 2026"; "2026-05-14 23:59:59" → "14 May 2026 23:59:59"
function _fmt_pd_date(s) {
	if (!s) return "";
	const parts = String(s).split(" ");
	const date_part = parts[0];
	const time_part = parts[1] || "";
	const d = new Date(date_part + "T00:00:00");
	if (isNaN(d.getTime())) return s;
	const formatted = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
	return time_part ? `${formatted} ${time_part}` : formatted;
}
