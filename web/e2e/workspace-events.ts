import type { Page } from "@playwright/test";

type FixtureWindow = Window & { emitFixtureWorkspaceEvent?: (frame: string) => void };

export async function mockWorkspaceEvents(page: Page, workspace: string) {
  await page.addInitScript((eventPath) => {
    const fetch = window.fetch.bind(window);
    const encoder = new TextEncoder();
    const browser = window as FixtureWindow;
    window.fetch = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.pathname !== eventPath) return fetch(input, init);
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          browser.emitFixtureWorkspaceEvent = (frame) => controller.enqueue(encoder.encode(frame));
          controller.enqueue(encoder.encode(": connected\n\n"));
          init?.signal?.addEventListener("abort", () => controller.close(), { once: true });
        },
      });
      return Promise.resolve(new Response(stream, { headers: { "Content-Type": "text/event-stream" } }));
    };
  }, `/api/v1/workspaces/${workspace}/events`);

  return async (event: { id: string; type: string } & Record<string, unknown>) => {
    const frame = `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
    await page.waitForFunction(() => "emitFixtureWorkspaceEvent" in window);
    await page.evaluate((frame) => {
      const emit = (window as FixtureWindow).emitFixtureWorkspaceEvent;
      if (!emit) throw new Error("The fixture event stream is not open.");
      emit(frame);
    }, frame);
  };
}
