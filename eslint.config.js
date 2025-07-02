import js from '@eslint/js';
import globals from 'globals';
import nojquery from 'eslint-plugin-no-jquery';
import vue from 'eslint-plugin-vue';

export default [
  // Base configuration applied to all files
  {
    linterOptions: {
      reportUnusedDisableDirectives: 'warn',
    },
    languageOptions: {
      ecmaVersion: 6,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.jquery,
      },
      parser: (await import('@babel/eslint-parser')).default,
      parserOptions: {
        sourceType: 'module',
        ecmaVersion: 6,
        babelOptions: {
          configFile: './.babelrc'
        }
      }
    },
    plugins: {
      'no-jquery': nojquery,
    },
    rules: {
      ...js.configs.recommended.rules,
      'prefer-template': 'error',
      'eqeqeq': ['error', 'always'],
      'quotes': ['error', 'single'],
      'eol-last': ['error', 'always'],
      'indent': ['error', 2],
      'no-console': 'error',
      'no-extra-semi': 'error',
      'no-mixed-spaces-and-tabs': 'error',
      'no-redeclare': 'error',
      'no-trailing-spaces': 'error',
      'no-undef': 'error',
      'no-unused-vars': 'error',
      'no-useless-escape': 'error',
      'space-in-parens': 'error',
      'vars-on-top': 'error',
      'prefer-const': 'error',
      'template-curly-spacing': 'error',
      'quote-props': ['error', 'as-needed'],
      'keyword-spacing': ['error', { 'before': true, 'after': true }],
      'key-spacing': ['error', { 'mode': 'strict' }],
    }
  },
  // Vue-specific configuration
  {
    files: ['**/*.vue'],
    plugins: {
      vue: vue,
    },
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.jquery,
      }
    },
    rules: {
      ...vue.configs['vue3-recommended'].rules,
      'vue/no-mutating-props': 'off',
      'vue/multi-word-component-names': ['error', {
        'ignores': ['Bookshelf', 'Shelf']
      }]
    }
  },
  // Ignore patterns (migrated from .eslintignore)
  {
    ignores: [
      '**/.*',
      'conf/**',
      'config/**',
      'docker/**',
      'infogami/**',
      'node_modules/**',
      'scripts/**',
      'static/build/**',
      'static/js/**',
      'static/build/vendor.js',
      'provisioning/**',
      'vendor/**',
      'tests/screenshots/**',
      'venv/**'
    ]
  }
];