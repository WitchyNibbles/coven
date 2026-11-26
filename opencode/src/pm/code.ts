/**
 * The longest a project code may be: the printed line limit of `src/coven/pm/text.py`, since a
 * longer code would be cut inside the command that carries it. A test reads that file.
 */
export const LINE_LIMIT = 200;

const PROJECT_CODE = /^[a-z0-9][a-z0-9-]*$/;

/** Whether `code` is a project code safe to put in a command (`is_valid_code` in `pm/text.py`). */
export function isValidCode(code: string): boolean {
  return code.length <= LINE_LIMIT && PROJECT_CODE.test(code);
}
