.DEFAULT_GOAL := help

UV ?= uv
ARGS ?=
CARGO ?= cargo
PICOTOOL ?= picotool
WIRED_FIXTURE ?= bench-rpi4b
WIRED_SOCKET ?= $(CURDIR)/local/polis-wired-state/daemon.sock
PICO_SERIAL ?=
WIRED_USER ?= $(shell id -un)
shell_quote = '$(subst ','"'"',$(1))'

COMMANDS := profiles configure build image audit prepare-eeprom \
	images devices flash snapshot smoke
WIRED_COMMANDS := wired-flash wired-daemon wired-usb-test wired-cli wired-diagnostics wired-benchmark
HELP_GOALS := help -h --help
COMMAND_HELP := $(filter $(HELP_GOALS),$(MAKECMDGOALS))
WIRED_HELP := $(COMMAND_HELP) $(filter $(HELP_GOALS),$(ARGS))

# Make consumes its own options before reading this file. After `--`, forward
# option-shaped goals and their values to one CLI command instead of executing
# them as additional Make targets (even when a value is named "build" or "help").
FIRST_GOAL := $(firstword $(MAKECMDGOALS))
ifneq ($(filter $(COMMANDS),$(FIRST_GOAL)),)
ifneq ($(filter -%,$(word 2,$(MAKECMDGOALS))),)
FORWARDED_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
endif
endif

ifneq ($(FORWARDED_ARGS),)
.PHONY: $(FIRST_GOAL) $(FORWARDED_ARGS)

$(FIRST_GOAL):
	@$(UV) run src/main.py $@ $(ARGS) $(foreach arg,$(FORWARDED_ARGS),$(call shell_quote,$(arg)))

$(filter-out $(FIRST_GOAL),$(FORWARDED_ARGS)):
	@:
else
.PHONY: $(HELP_GOALS) setup setup-permissions test clean purge $(WIRED_COMMANDS) $(COMMANDS)

$(HELP_GOALS):
ifeq ($(filter $(COMMANDS) $(WIRED_COMMANDS),$(MAKECMDGOALS)),)
	@printf '%s\n' 'Maintenance commands:' \
		'  make setup        Install Python dependencies, pinned usbboot tools, and RAM recovery payload' \
		'  make setup-permissions  Install host udev rules and enroll WIRED_USER via sudo' \
		'  make test         Run host tests' \
		'  make clean        Delete .cache/ and dl/ (downloads will be fetched again)' \
		'  make purge        Delete output/, local/usbboot/, local/polis-wired-tools/ (restore tools with make setup)' ''
	@printf '%s\n' 'Wired-control commands:' \
		'  make wired-flash   Build, flash, verify, and start Pico firmware using picotool' \
		'  make wired-daemon  Build and run the host daemon in the foreground' \
		'  make wired-usb-test  Check direct laptop/Pi USB data exchange' \
		'  make wired-diagnostics  Archive current Pi boot logs over USB' \
		'  make wired-benchmark  Run five cold boots with timing and log retrieval' \
		'  make wired-cli ARGS=status  Use the shared CLI/MCP frontend' \
		'' 'Details: make wired-flash help | make wired-daemon help' ''
	@$(UV) run src/main.py
else
	@:
endif

$(COMMANDS):
	@$(UV) run src/main.py $@ $(if $(COMMAND_HELP),--help,$(ARGS))

wired-flash:
ifneq ($(strip $(WIRED_HELP)),)
	@printf '%s\n' 'Usage: make wired-flash [PICO_SERIAL=BOOTSEL_SERIAL] [PICOTOOL=/path/to/picotool] [CARGO=/path/to/cargo]' \
		'Builds Pico 2 W firmware, writes it with picotool, verifies it, and starts it.' \
		'Put the Pico 2 W in BOOTSEL mode first. Flashing resets it and switches Pi power off.' \
		'PICO_SERIAL selects a specific Pico when multiple devices are attached.' \
		'Docs: package/polis-wired-control/README.md'
else
	cd package/polis-wired-control && $(CARGO) firmware --locked --target-dir target
	$(PICOTOOL) load -v -x -t elf package/polis-wired-control/target/thumbv8m.main-none-eabihf/firmware/polis-wired-firmware $(if $(PICO_SERIAL),--ser "$(PICO_SERIAL)")
endif

wired-usb-test:
ifneq ($(strip $(WIRED_HELP)),)
	@printf '%s\n' 'Usage: make wired-usb-test' \
		'Ping a profile with USB diagnostics over its direct Pi USB-C diagnostic endpoint.' \
		'Requires exactly one device with USB product Polis_Pi_Diagnostics; never selects the Pico.' \
		'Does not change power, flash storage, or provision OTP.'
else
	@$(UV) run python -m src.wired.usb
endif

wired-cli wired-diagnostics wired-benchmark:
ifneq ($(strip $(WIRED_HELP)),)
	@printf '%s\n' 'Usage: make wired-cli ARGS="status | console-read | ..."' \
		'       make wired-diagnostics' \
		'       make wired-benchmark [ARGS="--samples 3"]' \
		'Diagnostics reads the current boot capture. Benchmark cycles power (default: 5 boots); it never flashes.' \
		'Requires the running daemon, schema-3 Pico firmware, and rebuilt diagnostic profile for benchmarks.' \
		'Status reports progress and artifact paths. Never replay a benchmark after an ambiguous reply.'
else
	cd package/polis-wired-control && $(CARGO) build --locked --target-dir target -p polis-wired
	package/polis-wired-control/target/debug/polis-wired --socket "$(WIRED_SOCKET)" --fixture "$(WIRED_FIXTURE)" $(if $(filter wired-diagnostics,$@),diagnostics,$(if $(filter wired-benchmark,$@),benchmark --confirm-fixture "$(WIRED_FIXTURE)")) $(ARGS)
endif

wired-daemon:
ifneq ($(strip $(WIRED_HELP)),)
	@printf '%s\n' 'Usage: make wired-daemon [CARGO=/path/to/cargo]' \
		'Builds and runs the host daemon in the foreground; Ctrl-C stops it.' \
		'Connect the flashed Pico over USB; the daemon reconnects automatically.' \
		'Fixture constants: package/polis-wired-control/daemon/src/settings.rs. No config file needed.' \
		'Docs: package/polis-wired-control/docs/host.md'
else
	cd package/polis-wired-control && $(CARGO) build --locked --target-dir target -p polis-wired-daemon
	exec package/polis-wired-control/target/debug/polis-wired-daemon
endif

setup:
ifneq ($(strip $(COMMAND_HELP) $(filter $(HELP_GOALS),$(ARGS))),)
	@printf '%s\n' 'Usage: make setup [UV=/path/to/uv]' \
		'Install Python dependencies, download pinned usbboot/rpi-eeprom into local/usbboot/, and build rpiboot.' \
		'Prepare the verified RAM recovery payload in local/polis-wired-tools/gadget/.' \
		'Requires Git, Make, a C compiler, pkg-config, and libusb development files.' \
		'Existing clean checkouts at the pinned revisions are reused. No hardware is accessed.'
else
	@$(UV) sync
	@$(UV) run python -m src.setup.usbboot
	@$(UV) run python -m src.setup.recovery
endif

setup-permissions:
ifneq ($(strip $(COMMAND_HELP) $(filter $(HELP_GOALS),$(ARGS))),)
	@printf '%s\n' 'Usage: make setup-permissions [WIRED_USER=account]' \
		'Use sudo to install tracked udev rules and add the selected account to polis-wired.' \
		'WIRED_USER defaults to the user invoking make. Matching devices are accessible on any USB port.' \
		'Previous Polis rules are backed up and disabled. Rules apply when devices reconnect.' \
		'Daemon fixture identity checks remain required. Docs: package/polis-wired-control/docs/host.md'
else
	@sudo python3 -m src.setup.permissions --user $(call shell_quote,$(WIRED_USER))
endif

test:
	@$(UV) run python -m unittest discover -s tests -p 'test_*.py'

clean:
	@rm -rf -- .cache dl

purge:
	@rm -rf -- output local/usbboot local/polis-wired-tools
	@mkdir -p output
endif
