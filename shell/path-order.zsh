# Keep the launchers, and inside an agent session the command guards, at the
# front of PATH for the life of an interactive zsh.
#
# The env file puts them in front once, while the rc files are read. That is not
# enough under `mise activate zsh`: mise registers a precmd and chpwd hook that
# prepends its tool directories again at the first prompt and on every cd, so a
# mise-installed harness (omp) resolves to its raw binary ahead of the launcher
# from the first prompt on. This hook runs after mise's and undoes that.
#
# Re-sourcing moves the hook last without registering it twice: the env file is
# sourced from .zshenv, before .zshrc activates mise, and again at the end of
# .zshrc, which is the registration that has to win. Zsh preserves $? around
# hooks, so the prompt still sees the command's status.
autoload -Uz add-zsh-hook

typeset -g _AGENT_LAUNCHER_DIR="${${(%):-%x}:A:h:h}/launchers"
typeset -g _AGENT_GUARDS_DEFAULT_DIR="${${(%):-%x}:A:h:h:h}/agent-command-guards/shadows"

_agent_path_order() {
  # The guards go first, and only when an agent session already put them on
  # PATH: an ordinary shell must not acquire them.
  local guards="${AGENT_COMMAND_GUARDS_DIR:-$_AGENT_GUARDS_DEFAULT_DIR}"
  path=("$_AGENT_LAUNCHER_DIR" ${path:#"$_AGENT_LAUNCHER_DIR"})
  if (( ${path[(Ie)$guards]} )); then
    path=("$guards" ${path:#"$guards"})
  fi
  return 0
}

add-zsh-hook -d precmd _agent_path_order
add-zsh-hook -d chpwd _agent_path_order
add-zsh-hook precmd _agent_path_order
add-zsh-hook chpwd _agent_path_order
