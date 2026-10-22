use std::io::{self, Write};

use anyhow::Result;
use clap::{CommandFactory, Parser, Subcommand};
use rustyline::{DefaultEditor, error::ReadlineError};

use crate::{ActionCommand, Frontend};

#[derive(Parser)]
#[command(
    name = "",
    no_binary_name = true,
    disable_version_flag = true,
    about = "Commands for the selected Polis fixture",
    after_help = "Use help <command> for arguments and usage.\nUp/Down: history | Ctrl-R: search history | Ctrl-C: clear input | Ctrl-D: exit\nQuote text or paths containing spaces. Commands do not run through a shell.\nCommands use the fixture selected for this session."
)]
struct Input {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    #[command(flatten)]
    Action(ActionCommand),
    /// Leave the session (also: quit or Ctrl-D).
    #[command(visible_alias = "quit")]
    Exit,
}

pub(super) async fn run(frontend: &Frontend) -> Result<()> {
    let mut editor = DefaultEditor::new()?;
    let prompt = format!("polis[{}]> ", frontend.fixture);
    let mut output = io::stdout();
    writeln!(
        output,
        "Fixture: {}\nSocket: {}",
        frontend.fixture,
        frontend.socket.display()
    )?;
    Input::command().write_long_help(&mut output)?;
    writeln!(output)?;

    loop {
        output.flush()?;
        let line = match editor.readline(&prompt) {
            Ok(line) => line,
            Err(ReadlineError::Interrupted) => continue,
            Err(ReadlineError::Eof) => break,
            Err(error) => return Err(error.into()),
        };
        let words = match shlex::split(&line) {
            Some(words) if words.is_empty() => continue,
            Some(words) => words,
            None => {
                eprintln!("error: unclosed quote or unfinished escape; enter the command again");
                continue;
            }
        };
        editor.add_history_entry(line.as_str())?;
        let command = match Input::try_parse_from(words) {
            Ok(input) => input.command,
            Err(error) => {
                error.print()?;
                continue;
            }
        };
        match command {
            Command::Exit => break,
            Command::Action(command) => match frontend
                .request(command.into_action(&frontend.fixture))
                .await
            {
                Ok(value) => writeln!(output, "{}", serde_json::to_string_pretty(&value)?)?,
                Err(error) => eprintln!("error: {error:#}"),
            },
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoted_arguments_preserve_text_without_shell_expansion() {
        assert_eq!(shlex::split("  \t\n").unwrap(), Vec::<String>::new());
        assert_eq!(
            shlex::split(r#"console-write 'hello world' "" one\ two pre"fix" '$HOME; $(exit)'"#)
                .unwrap(),
            [
                "console-write",
                "hello world",
                "",
                "one two",
                "prefix",
                "$HOME; $(exit)"
            ]
        );
        assert_eq!(
            shlex::split(r#"console-write "say \"hello\" \\ \n""#).unwrap(),
            ["console-write", "say \"hello\" \\ \\n"]
        );
        assert_eq!(
            shlex::split("console-write 'héllo 🦀'").unwrap(),
            ["console-write", "héllo 🦀"]
        );
    }

    #[test]
    fn incomplete_input_is_rejected() {
        for line in ["console-write 'oops", "console-write \"oops", "status\\"] {
            assert!(shlex::split(line).is_none(), "{line}");
        }
    }

    #[test]
    fn help_and_validation_use_the_shared_commands() {
        Input::command().debug_assert();
        for line in ["help", "help run", "power --help"] {
            let error = Input::try_parse_from(shlex::split(line).unwrap())
                .err()
                .unwrap();
            assert_eq!(error.kind(), clap::error::ErrorKind::DisplayHelp);
        }
        for line in [
            "power invalid",
            "run --image /tmp/image",
            "mcp",
            "repl",
            "exit now",
        ] {
            assert!(
                Input::try_parse_from(shlex::split(line).unwrap()).is_err(),
                "{line}"
            );
        }
        for line in ["exit", "quit"] {
            assert!(matches!(
                Input::try_parse_from(shlex::split(line).unwrap())
                    .unwrap()
                    .command,
                Command::Exit
            ));
        }
    }
}
