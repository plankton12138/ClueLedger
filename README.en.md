# ClueLedger · 线簿

[简体中文](README.md) · [English](README.en.md)

**Keep ideas, characters, and clues connected to their sources throughout your novel.**

ClueLedger is an AI writing workspace for mystery fiction. Capture ideas, discuss your plot, read and revise chapters, save character profiles and worldbuilding notes, and review events, relationships, and clues in Story Overview—all in one place.

You decide where the story goes. ClueLedger brings your writing goals, existing material, and source passages together.

ClueLedger is built on [Tau Agent](https://github.com/huggingface/tau), with a writing experience designed for mystery fiction. Tau's MIT license and copyright information are preserved in the [third-party notices](THIRD_PARTY_NOTICES.md).

![ClueLedger writing workspace: chapters and modules on the left, the manuscript in the center, and a story discussion on the right](docs/screenshots/readme/writing.png)

*These screenshots show the current Chinese-language interface using the sample novel Letters on a Snowy Night (《雪夜来信》). English feature names below describe the corresponding Chinese controls.*

[Capture ideas](#capture-ideas) · [Write through conversation](#write-through-conversation) · [Save and reuse story material](#save-and-reuse-story-material) · [Review the whole story](#review-the-whole-story) · [Get started](#get-started)

## Capture ideas

A line of dialogue, a character's secret, or an unexplained detail can wait in your notes while you keep writing.

Click the floating **Ideas (灵感)** button to open a writing box and your recent notes. Drag the button or panel to a convenient spot, or press **Alt + I** to open and close it.

- **Save (记下)**: keep an idea for later.
- **Save and add to conversation (记下并加入对话)**: save it and attach it to the current conversation.
- **Add to conversation (加入对话)**: select an existing idea, then explain how you want to discuss or use it.

Edit notes in place, search them, review their history, or restore them from Recently Deleted. Adding an idea to a conversation does not send a message; you can finish your instructions before starting.

![Floating Ideas panel: capture a new thought, review recent notes, and choose what to add to the conversation](docs/screenshots/readme/ideas.png)

## Write through conversation

The writing workspace keeps your chapter list, manuscript, and conversation together. Start with a blank project, or import an existing manuscript and continue discussing or revising it.

Describe what you want to accomplish in the current request:

> Let's discuss the suspense in Chapter 1 first. Don't change the manuscript yet.

> Use Chapter 2 as a reference and change the end of Chapter 3 to a snowy night. Only edit Chapter 3 and keep the dialogue.

> Check the chronology across these three chapters. Point out issues with supporting passages, without editing anything yet.

Discussion, writing, and review can be requested separately. Revise the manuscript through conversation. When other chapters or notes matter, add them as references and specify what may be changed.

Use the left sidebar to switch between chapters and modules while keeping the manuscript in view. Hide the sidebar or conversation to focus on reading. Expand the task information in a conversation or open **Activity (活动记录)** to revisit what happened.

## Save and reuse story material

Save character profiles, worldbuilding, clue plans, and chapter outlines as modules. A module can hold one character or a collection of related notes.

Create a module with just a name and some text. Organize it with headings that suit your material. Once added to a project, it appears in the writing sidebar, with its headings available as navigation links.

**How to use it:** click the plus sign beside **Modules (模块)** in the sidebar, then choose existing material or create a new module. Return to the writing workspace to read it, or edit and save it directly.

Everyday edits affect only the current project. Choose **Save a copy to Module Library (另存到模块库)** when you want to reuse your revised material elsewhere. Multiple projects can use the same starting material while keeping their own changes.

![Module Library: browse character and clue notes, the inn setting, and chapter plans to add to a project](docs/screenshots/readme/module-library.png)

## Review the whole story

Open **Story Overview (故事总览)** to start with the timeline, then explore characters and clues. As the novel grows, follow an event to the people involved or check which chapters contain a particular clue.

### Follow chapter order or story chronology

Use chapter order to review how the narrative unfolds, or story chronology to examine when events happen. The **View up to (查看到)** selector limits the chapter range so you can revisit the story at an earlier point.

Inspect the source passages behind events and open related characters or clues. Statements, recollections, and conjectures retain their distinctions, helping you separate what someone says from established facts.

![Story Overview timeline: review chapter events and open related characters, clues, and source passages](docs/screenshots/readme/story-overview.png)

### Explore relationships and clue progress

The modules used by your project appear in Story Overview. Character material can be viewed as cards and relationship graphs; clue material shows its appearances and development. Other material remains available as readable text.

Author-defined details, future plans, and events already written in the manuscript are presented separately. Open a relationship's supporting passage to see where it comes from.

![Character cards and relationship graph: inspect Sakakibara's friendships, invitation, and supporting author notes](docs/screenshots/readme/characters.png)

After adding chapters or revising material, click **Update Overview (更新总览)** to organize the changes. Material remains readable before it has been organized, and changed sources are marked as needing an update. Opening the page or switching views does not start this work.

## Get started

1. **Open or create a project.** Use **Projects (作品)** to choose an existing project or create a folder for a new story. Add an existing manuscript through **Import (导入)** in the sidebar.
2. **Choose a model.** Connect and select your model in **Settings → Model Settings (设置 → 模型设置)**. You can still record ideas, edit modules, and read existing material before connecting.
3. **Capture your first idea.** Open Ideas and write down a story concept. Add it to a conversation when you want to discuss it.
4. **Set the goal for this request.** Ask for discussion, new writing, revisions to specific chapters, or a review. Add reference material when needed.
5. **Keep your notes and review progress.** Add reusable story material as modules. Visit Story Overview after writing to update and check events, characters, and clues.

## A few things to know

| Your question | How ClueLedger handles it |
|---|---|
| Does saving an idea automatically put it into the novel? | No. Add it to a conversation and explain how you want to use it. |
| Will discussing a possibility change the manuscript? | Ordinary discussion does not edit it. You can also explicitly say, “Let's discuss this first; don't make changes yet.” |
| Will editing a module affect other projects? | Saving within a project affects only that project. Save a copy to Module Library when you want to reuse the changes. |
| Does opening Story Overview start organizing the story? | No. Click Update Overview to start; you can stop it while it runs. |
| Can Story Overview decide whether my story makes sense? | It helps you explore relationships, review progress, and check source passages. Character motives, deductions, and the ending still need your judgment. |
