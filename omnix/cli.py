"""OMNIX CLI — multi-model, multi-agent local assistant."""

import sys

from rich.console import Console

from .agents import AGENT_CLASSES
from .config import AGENTS
from .memory import Memory
from .router import VALID_AGENTS, classify

console = Console()

AGENT_COLORS = {
    "chat": "cyan",
    "coding": "green",
    "reasoning": "magenta",
    "research": "yellow",
    "vision": "blue",
}

BANNER = r"""
   ____  __  __ _   _ _____  __
  / __ \|  \/  | \ | |_   _\ \/ /
 | |  | | \  / |  \| | | |  \  /
 | |  | | |\/| | . ` | | |  /  \
 | |__| | |  | | |\  |_| |_/ /\ \
  \____/|_|  |_|_| \_|_____/_/\_\

  Omniscient Modular Neural Intelligence eXecutive
  Local multi-model, multi-agent assistant. Type /help for commands.
"""

HELP_TEXT = """\
Commands:
  /agent <name>   Force the next message to a specific agent
                   (chat, coding, reasoning, research, vision)
  /agent auto     Return to automatic routing (default)
  /models         List agents and the model backing each
  /clear          Clear conversation history
  /voice          Enter speech-to-speech voice mode (Ctrl+C to return to text)
  /say <text>     Speak text out loud (quick TTS test, no agent involved)
  /help           Show this help
  /exit           Quit OMNIX
"""


def print_models():
    for name, spec in AGENTS.items():
        console.print(f"  [{AGENT_COLORS.get(name, 'white')}]{name:10}[/] -> {spec['model']}")


def run_turn(user_input: str, agent_name: str, agent, memory: Memory) -> str:
    model = AGENTS[agent_name]["model"]
    color = AGENT_COLORS.get(agent_name, "white")
    console.print(f"[{color}]omnix [{agent_name} · {model}] >[/] ", end="")

    full_response = []
    try:
        for chunk in agent.run(user_input, memory.get()):
            console.print(chunk, end="")
            full_response.append(chunk)
    except Exception as e:
        console.print(f"\n[red]error: {e}[/]")
        return ""

    console.print()
    response_text = "".join(full_response)
    memory.add_turn(user_input, response_text)
    return response_text


def voice_loop(agent_instances: dict, memory: Memory, forced_agent: str | None) -> None:
    from .voice import audio_io, stt, tts

    console.print("[dim]voice mode — listening... (Ctrl+C to return to text mode)[/]")
    try:
        while True:
            console.print("[dim]listening...[/]")
            audio = audio_io.record_until_silence()
            if audio is None:
                console.print("[dim]didn't hear anything, still listening...[/]")
                continue

            transcript = stt.transcribe(audio)
            if not transcript.strip():
                console.print("[dim]couldn't make that out, listening again...[/]")
                continue
            console.print(f"[bold white]you (voice) >[/] {transcript}")

            agent_name = forced_agent or classify(transcript)
            agent = agent_instances[agent_name]
            response_text = run_turn(transcript, agent_name, agent, memory)
            if response_text:
                tts.speak(response_text)
    except KeyboardInterrupt:
        console.print("\n[dim]exiting voice mode, back to text.[/]")


def main():
    console.print(BANNER, style="bold white")
    memory = Memory()
    forced_agent: str | None = None
    agent_instances = {name: cls() for name, cls in AGENT_CLASSES.items()}

    while True:
        try:
            user_input = console.input("[bold white]you >[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/]")
            sys.exit(0)

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()

            if cmd == "/exit":
                console.print("[dim]bye.[/]")
                break
            elif cmd == "/help":
                console.print(HELP_TEXT)
                continue
            elif cmd == "/models":
                print_models()
                continue
            elif cmd == "/clear":
                memory.clear()
                console.print("[dim]conversation history cleared.[/]")
                continue
            elif cmd == "/agent":
                if len(parts) < 2:
                    console.print("[red]usage: /agent <chat|coding|reasoning|research|vision|auto>[/]")
                    continue
                choice = parts[1].strip().lower()
                if choice == "auto":
                    forced_agent = None
                    console.print("[dim]routing set back to automatic.[/]")
                elif choice in VALID_AGENTS:
                    forced_agent = choice
                    console.print(f"[dim]forcing all messages to the {choice} agent.[/]")
                else:
                    console.print(f"[red]unknown agent '{choice}'. Try /models to see options.[/]")
                continue
            elif cmd == "/say":
                if len(parts) < 2:
                    console.print("[red]usage: /say <text>[/]")
                    continue
                try:
                    from .voice import tts

                    tts.speak(parts[1])
                except Exception as e:
                    console.print(f"[red]TTS error: {e}[/]")
                continue
            elif cmd == "/voice":
                try:
                    voice_loop(agent_instances, memory, forced_agent)
                except Exception as e:
                    console.print(f"[red]voice mode error: {e}[/]")
                continue
            else:
                console.print(f"[red]unknown command '{cmd}'. Try /help.[/]")
                continue

        agent_name = forced_agent or classify(user_input)
        agent = agent_instances[agent_name]
        run_turn(user_input, agent_name, agent, memory)


if __name__ == "__main__":
    main()
