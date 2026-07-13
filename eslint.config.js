import js from "@eslint/js";
import globals from "globals";

const securityRules = {
  "no-eval": "error",
  "no-implied-eval": "error",
  "no-new-func": "error",
  "no-script-url": "error",
};

export default [
  {
    ignores: [
      "custom_components/codex_bridge/frontend/**",
      "coverage/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  {
    files: ["frontend/src/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: globals.browser,
      sourceType: "module",
    },
    linterOptions: {
      reportUnusedDisableDirectives: "error",
    },
    rules: {
      ...js.configs.recommended.rules,
      ...securityRules,
      "no-constant-condition": "error",
      "no-dupe-keys": "error",
      "no-fallthrough": "error",
      "no-unreachable": "error",
      "no-unused-vars": ["error", { "argsIgnorePattern": "^_", "varsIgnorePattern": "^_" }],
      "no-unsafe-finally": "error",
    },
  },
  {
    files: ["frontend/test/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: {
        ...globals.browser,
        ...globals.node,
        ...globals.vitest,
      },
      sourceType: "module",
    },
    rules: {
      ...js.configs.recommended.rules,
      ...securityRules,
      "no-dupe-keys": "error",
      "no-fallthrough": "error",
      "no-unreachable": "error",
      "no-unused-vars": ["error", { "argsIgnorePattern": "^_", "varsIgnorePattern": "^_" }],
    },
  },
  {
    files: ["frontend/e2e/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
      sourceType: "module",
    },
    rules: {
      ...js.configs.recommended.rules,
      ...securityRules,
      "no-unused-vars": ["error", { "argsIgnorePattern": "^_", "varsIgnorePattern": "^_" }],
    },
  },
];
