.PHONY: tools
tools:  ## Install pinned gitleaks, OpenTofu and Trivy into ./.tools/bin (then: export PATH="$$PWD/.tools/bin:$$PATH")
	bash scripts/install_tools.sh
