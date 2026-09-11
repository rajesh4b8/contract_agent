import { Fragment } from "react";
import { Loader } from "../../shared/ui/loader";
import { Message } from "./provider";

interface Props {
    message: Message;
}

export function ChatMessage({ message }: Props) {
    const { type, parts, generating, } = message;
    return (
        <div className={`py-3 gap-0 ${type === "ai" ? "opacity-100" : "opacity-60"}`}>
            <strong className="text-xs">{type === "ai" ? "AI" : "USER"}</strong>
            <div>
                {parts.map(({ content, type }, index) => {
                    switch (type) {
                        case "tool_call":
                            return <details key={index} className="my-3 cursor-pointer">
                                <summary>Tool call</summary>
                                <code className="block p-1 bg-muted rounded-sm overflow-x-auto font-mono text-sm">{content}</code>
                            </details>
                        case "error":
                            // The backend sends this when a turn could not be
                            // produced — a spent model quota, a rejected key,
                            // a blocked prompt. It is the answer, so it is
                            // shown in place rather than swallowed.
                            return <p key={index} className="my-2 rounded-md border border-red-200 bg-red-50 p-2 text-sm text-red-700">
                                {content as string}
                            </p>
                        case "tool_message":
                            return <details key={index} className="my-3 cursor-pointer">
                                <summary>Tool message</summary>
                                <code className="block p-1 bg-muted rounded-sm overflow-x-auto font-mono text-sm">{content}</code>
                            </details>
                        default:
                            let displayContent: React.ReactNode = content;
                            if (typeof content === 'object' && content !== null) {
                                if (Array.isArray(content)) {
                                    displayContent = content.map((c: any) => c.text || JSON.stringify(c)).join('');
                                } else {
                                    displayContent = (content as any).text || JSON.stringify(content);
                                }
                            }
                            return <Fragment key={index}>{displayContent as string}</Fragment>;
                    }
                })}
                {generating && (
                    <Loader className="inline-flex" />
                )}
            </div>
        </div>
    );
}
