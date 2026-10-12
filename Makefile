.DEFAULT_GOAL := help

UV ?= uv
ARGS ?=
CARGO ?= cargo
PICOTOOL ?= picotool
SBC_CONTROLLER_FIXTURE ?= bench-rpi4b
SBC_CONTROLLER_SOCKET ?= $(CURDIR)/local/polis-sbc-controller-state/daemon.sock
PICO_SERIAL ?=
SBC_CONTROLLER_USER ?= $(shell id -un)
shell_quote = '$(subst ','"'"',$(1))'

COMMANDS := profiles configure build image audit prepare-eeprom \
	images devices flash snapshot smoke
SBC_CONTROLLER_COMMANDS := sbc-controller-flash sbc-controller-daemon sbc-controller-usb-test sbc-controller-cli sbc-controller-diagnostics sbc-controller-benchmark
HELP_GOALS := help -h --help
COMMAND_HELP := $(filter $(HELP_GOALS),$(MAKECMDGOALS))
SBC_CONTROLLER_HELP := $(COMMAND_HELP) $(filter $(HELP_GOALS),$(ARGS))

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
.PHONY: $(HELP_GOALS) setup setup-permissions test clean purge $(SBC_CONTROLLER_COMMANDS) $(COMMANDS)

$(HELP_GOALS):
ifeq ($(filter $(COMMANDS) $(SBC_CONTROLLER_COMMANDS),$(MAKECMDGOALS)),)
	@printf '%s\n' 'Maintenance commands:' \
		'  make setup        Install Python dependencies, pinned usbboot tools, and RAM recovery payload' \
		'  make setup-permissions  Install host udev rules and enroll SBC_CONTROLLER_USER via sudo' \
		'  make test         Run host tests' \
		'  make clean        Delete .cache/ and dl/ (downloads will be fetched again)' \
		'  make purge        Delete output/, local/usbboot/, local/polis-sbc-controller-tools/ (restore tools with make setup)' ''
	@printf '%s\n' 'SBC controller commands:' \
		'  make sbc-controller-flash   Build, flash, verify, and start Pico firmware using picotool' \
		'  make sbc-controller-daemon  Build and run the host daemon in the foreground' \
		'  make sbc-controller-usb-test  Check direct laptop/Pi USB data exchange' \
		'  make sbc-controller-diagnostics  Archive current Pi boot logs over USB' \
		'  make sbc-controller-benchmark  Run five cold boots; reject provisioning boots or missing setup status' \
		'  make sbc-controller-cli ARGS=status  Use the shared CLI/MCP frontend' \
		'' 'Details: make sbc-controller-flash help | make sbc-controller-daemon help' ''
	@$(UV) run src/main.py
else
	@:
endif

$(COMMANDS):
	@$(UV) run src/main.py $@ $(if $(COMMAND_HELP),--help,$(ARGS))

sbc-controller-flash:
ifneq ($(strip $(SBC_CONTROLLER_HELP)),)
	@printf '%s\n' 'Usage: make sbc-controller-flash [PICO_SERIAL=BOOTSEL_SERIAL] [PICOTOOL=/path/to/picotool] [CARGO=/path/to/cargo]' \
		'Builds Pico 2 W firmware, writes it with picotool, verifies it, and starts it.' \
		'Put the Pico 2 W in BOOTSEL mode first. Flashing resets it and switches Pi power off.' \
		'PICO_SERIAL selects a specific Pico when multiple devices are attached.' \
		'Docs: package/polis-sbc-controller/README.md'
else
	cd package/polis-sbc-controller && $(CARGO) firmware --locked --target-dir target
	$(PICOTOOL) load -v -x -t elf package/polis-sbc-controller/target/thumbv8m.main-none-eabihf/firmware/polis-sbc-controller-firmware $(if $(PICO_SERIAL),--ser "$(PICO_SERIAL)")
endif

sbc-controller-usb-test:
ifneq ($(strip $(SBC_CONTROLLER_HELP)),)
	@printf '%s\n' 'Usage: make sbc-controller-usb-test' \
		'Ping a profile with USB diagnostics over its direct Pi USB-C diagnostic endpoint.' \
		'Requires exactly one device with USB product Polis_Pi_Diagnostics; never selects the Pico.' \
		'Does not change power, flash storage, or provision OTP.'
else
	@$(UV) run python -m src.sbc_controller.usb
endif

sbc-controller-cli sbc-controller-diagnostics sbc-controller-benchmark:
ifneq ($(strip $(SBC_CONTROLLER_HELP)),)
	@printf '%s\n' 'Usage: make sbc-controller-cli ARGS="status | console-read | ..."' \
		'       make sbc-controller-diagnostics' \
		'       make sbc-controller-benchmark [ARGS="--samples 3"]' \
		'Diagnostics reads the current boot capture. Benchmark cycles power (default: 5 boots); it never flashes.' \
		'Requires the running daemon, schema-3 Pico firmware, and rebuilt diagnostic profile for benchmarks.' \
		'Build hardware-debug images with make image ARGS="--profile polis-rpi4-speed --development" to skip first-boot setup.' \
		'Status reports progress and artifact paths. Never replay a benchmark after an ambiguous reply.'
else
	cd package/polis-sbc-controller && $(CARGO) build --locked --target-dir target -p polis-sbc-controller
	package/polis-sbc-controller/target/debug/polis-sbc-controller --socket "$(SBC_CONTROLLER_SOCKET)" --fixture "$(SBC_CONTROLLER_FIXTURE)" $(if $(filter sbc-controller-diagnostics,$@),diagnostics,$(if $(filter sbc-controller-benchmark,$@),benchmark --confirm-fixture "$(SBC_CONTROLLER_FIXTURE)")) $(ARGS)
endif

sbc-controller-daemon:
ifneq ($(strip $(SBC_CONTROLLER_HELP)),)
	@printf '%s\n' 'Usage: make sbc-controller-daemon [CARGO=/path/to/cargo]' \
		'Builds and runs the host daemon in the foreground; Ctrl-C stops it.' \
		'Connect the flashed Pico over USB; the daemon reconnects automatically.' \
		'Fixture constants: package/polis-sbc-controller/daemon/src/settings.rs. No config file needed.' \
		'Docs: package/polis-sbc-controller/docs/host.md'
else
	cd package/polis-sbc-controller && $(CARGO) build --locked --target-dir target -p polis-sbc-controller-daemon
	exec package/polis-sbc-controller/target/debug/polis-sbc-controller-daemon
endif

setup:
ifneq ($(strip $(COMMAND_HELP) $(filter $(HELP_GOALS),$(ARGS))),)
	@printf '%s\n' 'Usage: make setup [UV=/path/to/uv]' \
		'Install Python dependencies, download pinned usbboot/rpi-eeprom into local/usbboot/, and build rpiboot.' \
		'Prepare the verified RAM recovery payload in local/polis-sbc-controller-tools/gadget/.' \
		'Requires Git, Make, a C compiler, pkg-config, and libusb development files.' \
		'Existing clean checkouts at the pinned revisions are reused. No hardware is accessed.'
else
	@$(UV) sync
	@$(UV) run python -m src.setup.usbboot
	@$(UV) run python -m src.setup.recovery
endif

setup-permissions:
ifneq ($(strip $(COMMAND_HELP) $(filter $(HELP_GOALS),$(ARGS))),)
	@printf '%s\n' 'Usage: make setup-permissions [SBC_CONTROLLER_USER=account]' \
		'Use sudo to install tracked udev rules and add the selected account to polis-sbc-controller.' \
		'SBC_CONTROLLER_USER defaults to the user invoking make. Matching devices are accessible on any USB port.' \
		'Previous Polis rules are backed up and disabled. Rules apply when devices reconnect.' \
		'Daemon fixture identity checks remain required. Docs: package/polis-sbc-controller/docs/host.md'
else
	@sudo python3 -m src.setup.permissions --user $(call shell_quote,$(SBC_CONTROLLER_USER))
endif

test:
	@$(UV) run python -m unittest discover -s tests -p 'test_*.py'

clean:
	@rm -rf -- .cache dl

purge:
	@rm -rf -- output local/usbboot local/polis-sbc-controller-tools
	@mkdir -p output
endif
