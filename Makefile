.DEFAULT_GOAL := help

UV ?= uv
ARGS ?=

COMMANDS := boards configure build menuconfig savedefconfig \
	linux-reconfigure busybox-reconfigure images devices flash snapshot smoke
HELP_GOALS := help -h --help
COMMAND_HELP := $(filter $(HELP_GOALS),$(MAKECMDGOALS))

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
shell_quote = '$(subst ','"'"',$(1))'
.PHONY: $(FIRST_GOAL) $(FORWARDED_ARGS)

$(FIRST_GOAL):
	@$(UV) run src/main.py $@ $(ARGS) $(foreach arg,$(FORWARDED_ARGS),$(call shell_quote,$(arg)))

$(filter-out $(FIRST_GOAL),$(FORWARDED_ARGS)):
	@:
else
.PHONY: $(HELP_GOALS) setup test clean purge $(COMMANDS)

$(HELP_GOALS):
ifeq ($(filter $(COMMANDS),$(MAKECMDGOALS)),)
	@$(UV) run src/main.py
else
	@:
endif

$(COMMANDS):
	@$(UV) run src/main.py $@ $(if $(COMMAND_HELP),--help,$(ARGS))

setup:
	@$(UV) sync

test:
	@$(UV) run python -m unittest discover -s tests -p 'test_*.py'

clean:
	@rm -rf -- .cache

purge:
	@rm -rf -- output
	@mkdir -p output
endif
