"use client";

import { CheckOutlined, DownOutlined, FilterOutlined } from "@ant-design/icons";
import { Select, type RefSelectProps, type SelectProps } from "antd";
import type { Ref } from "react";

type WorkbenchOption<Value extends string> = { value: Value; label: string };
type WorkbenchSelectProps<Value extends string> = Pick<
  SelectProps<Value, WorkbenchOption<Value>>,
  "value" | "options" | "onChange" | "placeholder" | "disabled" | "status" |
    "popupMatchSelectWidth" | "optionRender" | "aria-describedby" | "aria-invalid" | "aria-required"
> & {
  label: string;
  filter?: boolean;
  active?: boolean;
  ref?: Ref<RefSelectProps>;
};

export function WorkbenchSelect<Value extends string>({
  label, filter, active, ref, ...props
}: WorkbenchSelectProps<Value>) {
  return (
    <Select<Value, WorkbenchOption<Value>> {...props} ref={ref} aria-label={label}
      variant="borderless" showSearch={false} virtual={false}
      className={filter ? `workbench-select-filter${active ? " workbench-select-active" : ""}` : undefined}
      classNames={{
        root: "workbench-select", prefix: "workbench-select-prefix", suffix: "workbench-select-caret",
        popup: { root: "workbench-select-menu", listItem: "workbench-select-option" },
      }}
      styles={{ content: { opacity: 1 } }}
      prefix={filter ? <FilterOutlined aria-hidden="true" /> : undefined}
      suffix={<DownOutlined aria-hidden="true" />}
      menuItemSelectedIcon={<CheckOutlined aria-hidden="true" />}
    />
  );
}
