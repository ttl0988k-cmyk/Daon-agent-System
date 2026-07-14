/**
 * Monaco Editor UX Integration for Hermes App
 * Hermes 앱에 Monaco Editor UX 핵심 기능 통합
 * 
 * 기능:
 * - AI 응답을 executeEdits()로 적용
 * - Undo/Redo 스택 관리
 * - Dirty 상태 관리
 * - AI 프로바이더 지원 (DeepSeek, Claude, GLM, GPT)
 */

// ─────────────────────────────────────────────────────────────
//  MonacoEditorUX Namespace
// ─────────────────────────────────────────────────────────────
window.MonacoEditorUX = {
    version: '1.0.0-hermes-integration',

    // State
    _editor: null,
    _undoStacks: new Map(),  // path -> operation stacks
    _redoStacks: new Map(),
    _dirtyFiles: new Set(),
    _aiResultParser: null,

    /**
     * Monaco 에디터 초기화
     */
    init: function (editor) {
        this._editor = editor;
        this._aiResultParser = new AIResultParser();

        // 에디터 변경 이벤트 리스너
        editor.onDidChangeModelContent((e) => {
            this._onContentChanged(e);
        });

        console.log('[MonacoEditorUX] Initialized with editor');
        return this;
    },

    /**
     * 에디터 인스턴스获取
     */
    getEditor: function () {
        return this._editor;
    },

    /**
     * 콘텐츠 변경 이벤트
     */
    _onContentChanged: function (e) {
        // 변경 사항에 대한 undo 스택 관리
        const model = this._editor.getModel();
        if (!model) return;

        const path = model.uri.toString();

        // Operations을 undo 스택에 추가
        if (!this._undoStacks.has(path)) {
            this._undoStacks.set(path, []);
        }

        e.changes.forEach(change => {
            this._undoStacks.get(path).push({
                range: change.range,
                text: change.text,
                forceMoveMarkers: true
            });
        });

        // Redo 스택をクリア
        if (this._redoStacks.has(path)) {
            this._redoStacks.set(path, []);
        }

        // Dirty 상태 업데이트
        this._markDirty(path);

        console.log('[MonacoEditorUX] Content changed:', path, e.changes.length, 'changes');
    },

    /**
     * Dirty 상태로 표시
     */
    _markDirty: function (path) {
        this._dirtyFiles.add(path);
        this._updateDirtyIndicator(path, true);
    },

    /**
     * Dirty 상태 해제
     */
    _clearDirty: function (path) {
        this._dirtyFiles.delete(path);
        this._updateDirtyIndicator(path, false);
    },

    /**
     * Dirty 인디케이터 업데이트
     */
    _updateDirtyIndicator: function (path, isDirty) {
        // Hermes 앱의 탭 UI 업데이트
        const tabs = State.openTabs;
        const tab = tabs.find(t => t.path === path || t.name === path);
        if (tab) {
            tab.dirty = isDirty;
            if (typeof renderTabs === 'function') {
                renderTabs();
            }
        }
    },

    /**
     * AI 응답 적용
     * @param {object} response - AI 응답 { provider, content, action, path }
     */
    applyAIResponse: function (response) {
        if (!this._editor) {
            console.error('[MonacoEditorUX] Editor not initialized');
            return;
        }

        try {
            // AI 결과 파싱
            const parsed = this._aiResultParser.parse(response);

            if (!parsed.success) {
                console.error('[MonacoEditorUX] Failed to parse AI response:', parsed.error);
                return;
            }

            const { operations, filePath } = parsed;

            console.log('[MonacoEditorUX] Applying', operations.length, 'operations for', filePath);

            // executeEdits로 операции 적용
            this._editor.executeEdits('ai-edit', operations);

            // Dirty 상태 업데이트
            this._markDirty(filePath);

            console.log('[MonacoEditorUX] AI response applied successfully');
        } catch (err) {
            console.error('[MonacoEditorUX] Error applying AI response:', err);
        }
    },

    /**
     * Undo
     */
    undo: function (path) {
        const stack = this._undoStacks.get(path);
        if (!stack || stack.length === 0) {
            console.log('[MonacoEditorUX] Nothing to undo');
            return;
        }

        const operation = stack.pop();

        // Inverse operation 생성
        const model = this._editor.getModel();
        const originalText = model.getValueInRange(operation.range);

        // Redo 스택에 추가
        if (!this._redoStacks.has(path)) {
            this._redoStacks.set(path, []);
        }
        this._redoStacks.get(path).push({
            range: operation.range,
            text: originalText,
            forceMoveMarkers: true
        });

        //Undo 적용
        this._editor.executeEdits('undo', [{
            range: operation.range,
            text: '',
            forceMoveMarkers: true
        }]);

        console.log('[MonacoEditorUX] Undo performed');
    },

    /**
     * Redo
     */
    redo: function (path) {
        const stack = this._redoStacks.get(path);
        if (!stack || stack.length === 0) {
            console.log('[MonacoEditorUX] Nothing to redo');
            return;
        }

        const operation = stack.pop();

        // Undo 스택에 추가
        if (!this._undoStacks.has(path)) {
            this._undoStacks.set(path, []);
        }
        this._undoStacks.get(path).push(operation);

        // Redo 적용
        this._editor.executeEdits('redo', [operation]);

        console.log('[MonacoEditorUX] Redo performed');
    },

    /**
     * Dirty 파일 목록取得
     */
    getDirtyFiles: function () {
        return Array.from(this._dirtyFiles);
    },

    /**
     * 파일이 Dirty인지 확인
     */
    isDirty: function (path) {
        return this._dirtyFiles.has(path);
    },

    /**
     * 파일 저장 (dirty 클리어)
     */
    markSaved: function (path) {
        this._clearDirty(path);
        // Undo/Redo 스택 클리어
        this._undoStacks.delete(path);
        this._redoStacks.delete(path);
    }
};


// ─────────────────────────────────────────────────────────────
//  AI Result Parser
//  AI 결과를 파싱하여 edit operations으로 변환
// ─────────────────────────────────────────────────────────────
class AIResultParser {
    constructor() {
        this.providers = {
            deepseek: this._parseDeepSeek.bind(this),
            claude: this._parseClaude.bind(this),
            glm: this._parseGLM.bind(this),
            gpt: this._parseGPT.bind(this),
            hermes: this._parseHermes.bind(this)
        };
    }

    /**
     * AI 응답 파싱
     */
    parse(response) {
        const { provider, content, action, path } = response;

        if (!content) {
            return { success: false, error: 'No content to parse' };
        }

        const parser = this.providers[provider?.toLowerCase()];
        if (!parser) {
            return { success: false, error: `Unknown provider: ${provider}` };
        }

        try {
            const operations = parser(content, action, path);
            return { success: true, operations, filePath: path };
        } catch (err) {
            return { success: false, error: err.message };
        }
    }

    /**
     * DeepSeek 응답 파싱
     */
    _parseDeepSeek(content, action, path) {
        // DeepSeek는 일반적으로 수정된 코드 전체를 반환
        // 파일 전체 교체 또는 부분 수정
        const operations = [];

        if (action === 'replace' || action === 'create') {
            // 전체 파일 교체
            operations.push({
                range: {
                    startLineNumber: 1,
                    startColumn: 1,
                    endLineNumber: 999999,
                    endColumn: 1
                },
                text: content,
                forceMoveMarkers: true
            });
        } else if (action === 'modify') {
            // 부분 수정 - 먼저 더iff 계산 필요
            // 간단한 heuristic: 첫 번째 줄부터 끝까지
            operations.push({
                range: {
                    startLineNumber: 1,
                    startColumn: 1,
                    endLineNumber: 999999,
                    endColumn: 1
                },
                text: content,
                forceMoveMarkers: true
            });
        }

        return operations;
    }

    /**
     * Claude 응답 파싱
     */
    _parseClaude(content, action, path) {
        // Claude는 XML 태그로 코드 블록을 감싸서 반환
        // ```language\ncode\n``` 형식
        const operations = [];
        const codeBlocks = this._extractCodeBlocks(content);

        if (codeBlocks.length > 0) {
            // 코드 블록이 있으면 첫 번째 블록 사용
            const code = codeBlocks[0];
            operations.push({
                range: {
                    startLineNumber: 1,
                    startColumn: 1,
                    endLineNumber: 999999,
                    endColumn: 1
                },
                text: code,
                forceMoveMarkers: true
            });
        } else {
            // 코드 블록이 없으면 전체 내용 사용
            operations.push({
                range: {
                    startLineNumber: 1,
                    startColumn: 1,
                    endLineNumber: 999999,
                    endColumn: 1
                },
                text: content,
                forceMoveMarkers: true
            });
        }

        return operations;
    }

    /**
     * GLM 응답 파싱
     */
    _parseGLM(content, action, path) {
        // GLM은 일반 텍스트 또는 마크다운 형식
        return this._parseDeepSeek(content, action, path);
    }

    /**
     * GPT 응답 파싱
     */
    _parseGPT(content, action, path) {
        // GPT는 markdown 코드 블록 또는 일반 텍스트
        const operations = [];
        const codeBlocks = this._extractCodeBlocks(content);

        if (codeBlocks.length > 0) {
            const code = codeBlocks[0];
            operations.push({
                range: {
                    startLineNumber: 1,
                    startColumn: 1,
                    endLineNumber: 999999,
                    endColumn: 1
                },
                text: code,
                forceMoveMarkers: true
            });
        } else {
            operations.push({
                range: {
                    startLineNumber: 1,
                    startColumn: 1,
                    endLineNumber: 999999,
                    endColumn: 1
                },
                text: content,
                forceMoveMarkers: true
            });
        }

        return operations;
    }

    /**
     * Hermes 앱 응답 파싱 (file_edit SSE 이벤트)
     * 백엔드에서 전송된 file_edit 이벤트: {name: 'write_file', args: {path, content}}
     */
    _parseHermes(content, action, path) {
        const operations = [];

        // 파일 전체 내용이 content에 들어있음 — 전체 교체
        const model = this._getEditorModel();
        const lineCount = model ? model.getLineCount() : 1;

        operations.push({
            range: {
                startLineNumber: 1,
                startColumn: 1,
                endLineNumber: Math.max(lineCount, 999999),
                endColumn: 1
            },
            text: content || '',
            forceMoveMarkers: true
        });

        return operations;
    }

    /**
     * 현재 에디터 모델 가져오기 (AIResultParser는 MonacoEditorUX를 참조하지 않으므로)
     */
    _getEditorModel() {
        if (window.MonacoEditorUX && window.MonacoEditorUX._editor) {
            return window.MonacoEditorUX._editor.getModel();
        }
        return null;
    }

    /**
     * 마크다운 코드 블록 추출
     */
    _extractCodeBlocks(text) {
        const blocks = [];
        const regex = /```(?:\w+)?\n?([\s\S]*?)```/g;
        let match;

        while ((match = regex.exec(text)) !== null) {
            blocks.push(match[1].trim());
        }

        return blocks;
    }
}


// ─────────────────────────────────────────────────────────────
//  Hermes App Integration
// ─────────────────────────────────────────────────────────────

// Hermes 앱의 Monaco 초기화 후 MonacoEditorUX 초기화
const originalInitMonaco = window.initMonaco;
window.initMonaco = function () {
    originalInitMonaco.call(window);

    // MonacoEditorUX 초기화
    if (State.editor) {
        MonacoEditorUX.init(State.editor);
        console.log('[Hermes] MonacoEditorUX integration complete');
    }
};

// Hermes 앱에 전역 함수 노출
window.applyAIResponse = function (response) {
    MonacoEditorUX.applyAIResponse(response);
};

window.getMonacoEditorUX = function () {
    return MonacoEditorUX;
};

console.log('[MonacoEditorUX] Hermes integration loaded');
