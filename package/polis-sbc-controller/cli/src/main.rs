use anyhow::{Result, ensure};
use clap::{Parser, Subcommand};
use polis_sbc_controller_host_api::{API_VERSION, Action, Image, Request, call};
use rmcp::{
    ServiceExt,
    handler::server::wrapper::Parameters,
    model::{CallToolResult, ContentBlock},
    schemars, tool, tool_router,
};
use serde::Deserialize;
use std::path::PathBuf;

#[derive(Parser)]
#[command(about = "CLI and MCP frontend for one explicitly selected Polis fixture")]
struct Args {
    #[arg(long)]
    socket: PathBuf,
    #[arg(long)]
    fixture: String,
    #[command(subcommand)]
    command: Cli,
}
#[derive(Subcommand)]
enum Cli {
    Status,
    Images,
    /// Retrieve current boot firmware/kernel logs over the dedicated Pi USB port.
    Diagnostics,
    /// Cold boot repeatedly; archive entry timings and current-boot diagnostics.
    Benchmark {
        #[arg(long, default_value_t = 5)]
        samples: u32,
        #[arg(long)]
        confirm_fixture: String,
    },
    /// Commission recovery: power cycle and load the pinned RAM gadget; never flash.
    RecoveryUsb {
        #[arg(long)]
        confirm_fixture: String,
    },
    ConsoleRead {
        #[arg(long, default_value_t = 0)]
        after: u64,
        #[arg(long, default_value_t = 4096)]
        max_bytes: usize,
    },
    ConsoleWrite {
        text: String,
    },
    Power {
        #[arg(value_parser = ["on", "off"])]
        state: String,
        #[arg(long)]
        confirm_fixture: String,
    },
    Cycle {
        #[arg(long, default_value_t = 1000)]
        off_ms: u32,
        #[arg(long)]
        confirm_fixture: String,
    },
    /// Flash a selected image then boot, or boot-only if no image is supplied.
    Run {
        #[arg(long, requires = "sha256")]
        image: Option<PathBuf>,
        #[arg(long, requires = "image")]
        sha256: Option<String>,
        #[arg(long)]
        auto_recover: bool,
        #[arg(long)]
        confirm_fixture: String,
    },
    Mcp,
}
#[derive(Clone)]
struct Frontend {
    socket: PathBuf,
    fixture: String,
}
impl Frontend {
    async fn request(&self, action: Action) -> Result<serde_json::Value> {
        // No retries: loss of a reply can mean the operation already started.
        call(
            &self.socket,
            Request {
                api_version: API_VERSION,
                fixture: self.fixture.clone(),
                action,
            },
        )
        .await
    }
    async fn tool_result(&self, action: Action) -> CallToolResult {
        match self.request(action).await {
            Ok(value) => CallToolResult::success(vec![ContentBlock::text(value.to_string())]),
            Err(error) => CallToolResult::error(vec![ContentBlock::text(format!("{error:#}"))]),
        }
    }
}
#[derive(Deserialize, schemars::JsonSchema)]
struct RunArgs {
    image: Option<String>,
    sha256: Option<String>,
    #[serde(default)]
    auto_recover: bool,
    confirm_fixture: String,
}
#[derive(Deserialize, schemars::JsonSchema)]
struct BenchmarkArgs {
    samples: u32,
    confirm_fixture: String,
}
#[derive(Deserialize, schemars::JsonSchema)]
struct ReadArgs {
    after: u64,
    max_bytes: usize,
}
#[derive(Deserialize, schemars::JsonSchema)]
struct WriteArgs {
    text: String,
}
#[derive(Deserialize, schemars::JsonSchema)]
struct PowerArgs {
    on: bool,
    confirm_fixture: String,
}
#[derive(Deserialize, schemars::JsonSchema)]
struct RecoveryUsbArgs {
    confirm_fixture: String,
}
#[derive(Deserialize, schemars::JsonSchema)]
struct CycleArgs {
    off_ms: u32,
    confirm_fixture: String,
}

#[tool_router(server_handler)]
impl Frontend {
    #[tool(
        description = "Retrieve current-boot firmware/kernel diagnostics over the fixture's pinned Pi USB connection; return archive path, hashes and metrics. Refused while fixture busy."
    )]
    async fn diagnostics(&self) -> CallToolResult {
        self.tool_result(Action::Diagnostics).await
    }
    #[tool(
        description = "POWER CYCLES: benchmark 1..20 normal cold boots with kernel-entry marker and hashed diagnostic retrieval. No flash or automatic recovery. Returns run ID; poll status. Never replay after ambiguous disconnect."
    )]
    async fn benchmark(&self, Parameters(args): Parameters<BenchmarkArgs>) -> CallToolResult {
        self.tool_result(Action::Benchmark {
            samples: args.samples,
            confirm_fixture: args.confirm_fixture,
        })
        .await
    }
    #[tool(
        description = "Read fixture connection, recovery workflow status, failure details, and UART log path"
    )]
    async fn status(&self) -> CallToolResult {
        self.tool_result(Action::Status).await
    }
    #[tool(
        description = "POWER CYCLE: enter GPIO-forced recovery and load the pinned RAM USB gadget for commissioning. Does not write storage or OTP. Returns run ID; poll status. Never retry after an ambiguous disconnect."
    )]
    async fn recovery_usb(&self, Parameters(args): Parameters<RecoveryUsbArgs>) -> CallToolResult {
        self.tool_result(Action::RecoveryUsb {
            confirm_fixture: args.confirm_fixture,
        })
        .await
    }
    #[tool(description = "List repository images/snapshots without selecting or flashing one")]
    async fn images(&self) -> CallToolResult {
        self.tool_result(Action::Images).await
    }
    #[tool(description = "Read bounded captured UART output using a cursor; reports gaps")]
    async fn console_read(&self, Parameters(args): Parameters<ReadArgs>) -> CallToolResult {
        self.tool_result(Action::ConsoleRead {
            after: args.after,
            max_bytes: args.max_bytes,
        })
        .await
    }
    #[tool(
        description = "Write up to 128 UTF-8 bytes to the Pi UART; refused during a recovery workflow"
    )]
    async fn console_write(&self, Parameters(args): Parameters<WriteArgs>) -> CallToolResult {
        self.tool_result(Action::ConsoleWrite { text: args.text })
            .await
    }
    #[tool(
        description = "DESTRUCTIVE: change Pi power. Requires confirm_fixture equal to the explicitly selected fixture"
    )]
    async fn power(&self, Parameters(args): Parameters<PowerArgs>) -> CallToolResult {
        self.tool_result(Action::Power {
            on: args.on,
            confirm_fixture: args.confirm_fixture,
        })
        .await
    }
    #[tool(
        description = "DESTRUCTIVE: cycle Pi power with current boot selection; never retried automatically"
    )]
    async fn cycle(&self, Parameters(args): Parameters<CycleArgs>) -> CallToolResult {
        self.tool_result(Action::Cycle {
            off_ms: args.off_ms,
            confirm_fixture: args.confirm_fixture,
        })
        .await
    }
    #[tool(
        description = "DESTRUCTIVE: start flash/normal-boot/diagnostics. Optional auto_recover permits ONE known-good flash after a readiness timeout. Explicit fixture confirmation and image SHA-256 required. Returns a run ID; poll status. Never retry after a lost reply."
    )]
    async fn run(&self, Parameters(args): Parameters<RunArgs>) -> CallToolResult {
        let image = match (args.image, args.sha256) {
            (Some(path), Some(sha256)) => Some(Image {
                path: path.into(),
                sha256,
            }),
            (None, None) => None,
            _ => {
                return CallToolResult::error(vec![ContentBlock::text(
                    "image and sha256 must be supplied together",
                )]);
            }
        };
        self.tool_result(Action::Workflow {
            image,
            auto_recover: args.auto_recover,
            confirm_fixture: args.confirm_fixture,
        })
        .await
    }
}
#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    ensure!(!args.fixture.is_empty(), "explicit fixture required");
    let frontend = Frontend {
        socket: args.socket,
        fixture: args.fixture,
    };
    let action = match args.command {
        Cli::Mcp => {
            frontend
                .serve(rmcp::transport::stdio())
                .await?
                .waiting()
                .await?;
            return Ok(());
        }
        Cli::Status => Action::Status,
        Cli::Images => Action::Images,
        Cli::Diagnostics => Action::Diagnostics,
        Cli::Benchmark {
            samples,
            confirm_fixture,
        } => Action::Benchmark {
            samples,
            confirm_fixture,
        },
        Cli::RecoveryUsb { confirm_fixture } => Action::RecoveryUsb { confirm_fixture },
        Cli::ConsoleRead { after, max_bytes } => Action::ConsoleRead { after, max_bytes },
        Cli::ConsoleWrite { text } => Action::ConsoleWrite { text },
        Cli::Power {
            state,
            confirm_fixture,
        } => Action::Power {
            on: state == "on",
            confirm_fixture,
        },
        Cli::Cycle {
            off_ms,
            confirm_fixture,
        } => Action::Cycle {
            off_ms,
            confirm_fixture,
        },
        Cli::Run {
            image,
            sha256,
            auto_recover,
            confirm_fixture,
        } => Action::Workflow {
            image: image.map(|path| Image {
                path,
                sha256: sha256.unwrap(),
            }),
            auto_recover,
            confirm_fixture,
        },
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&frontend.request(action).await?)?
    );
    Ok(())
}
