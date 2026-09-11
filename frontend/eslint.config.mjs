import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      ...jsxA11y.configs.recommended.rules,
      // shadcn/Radix wrappers behave as form controls; teach the rule to
      // accept htmlFor associations targeting them.
      "jsx-a11y/label-has-associated-control": [
        "error",
        {
          controlComponents: [
            "Input",
            "Textarea",
            "Select",
            "SelectTrigger",
            "Switch",
            "Checkbox",
            "RadioGroup",
            "Label",
          ],
          assert: "either",
          depth: 25,
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
