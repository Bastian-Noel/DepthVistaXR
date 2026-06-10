import ctypes
import threading
import time

import numpy as np
import torch


class SBSWindowOutput:
    def __init__(self, lock, width, height, **_unused):
        self.lock = lock
        self.frame_lock = threading.Lock()
        self.width = width
        self.height = height
        self.frame = None
        self.frame_version = 0
        self.stop_event = threading.Event()
        self.closed_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread = None
        self.error = None
        self.frame_times = []

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.closed_event.clear()
        self.ready_event.clear()
        self.thread = threading.Thread(
            target=self._run, name="depthvista-sbs-window", daemon=False
        )
        self.thread.start()
        if not self.ready_event.wait(timeout=5.0):
            raise RuntimeError("SBS window did not initialize")
        if self.error is not None:
            raise RuntimeError(f"SBS window failed: {self.error}")

    def stop(self):
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=4.0)
        self.closed_event.set()

    def set_frame_data(self, frame_data):
        frame, _timestamp = frame_data
        with torch.inference_mode():
            prepared = frame.detach()
            if prepared.dtype != torch.uint8:
                prepared = prepared.clamp(0, 1).mul(255).round().to(torch.uint8)
            if prepared.ndim == 3 and prepared.shape[0] == 3:
                prepared = prepared.permute(1, 2, 0).contiguous()
            if prepared.ndim != 3 or prepared.shape[2] != 3:
                raise ValueError(
                    f"SBS window expects RGB CHW/HWC, got {tuple(prepared.shape)}"
                )
            prepared = prepared.cpu().numpy()
        with self.frame_lock:
            self.frame = prepared
            self.frame_version += 1

    def get_fps(self):
        now = time.perf_counter()
        self.frame_times = [
            frame_time
            for frame_time in self.frame_times
            if now - frame_time <= 1.0
        ]
        return float(len(self.frame_times))

    def is_closed(self):
        return self.closed_event.is_set()

    def _run(self):
        glfw = None
        window = None
        program = None
        texture = None
        vao = None
        vbo = None
        try:
            import glfw
            from OpenGL import GL

            if not glfw.init():
                raise RuntimeError("GLFW initialization failed")
            glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
            glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
            glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
            aspect = self.width / max(self.height, 1)
            window_width = min(max(self.width // 2, 960), 1600)
            window_height = max(int(window_width / aspect), 360)
            window = glfw.create_window(
                window_width,
                window_height,
                "DepthVista XR - SBS",
                None,
                None,
            )
            if not window:
                raise RuntimeError("Unable to create SBS OpenGL window")
            glfw.make_context_current(window)
            glfw.swap_interval(1)
            program = self._create_program(GL)
            vao, vbo = self._create_quad(GL)
            texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR
            )
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR
            )
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE
            )
            GL.glTexParameteri(
                GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE
            )
            texture_size = None
            displayed_version = -1
            self.ready_event.set()

            while not self.stop_event.is_set() and not glfw.window_should_close(
                window
            ):
                glfw.poll_events()
                with self.frame_lock:
                    if self.frame_version != displayed_version:
                        frame = self.frame
                        displayed_version = self.frame_version
                    else:
                        frame = None
                if frame is not None:
                    frame_height, frame_width = frame.shape[:2]
                    GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
                    GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
                    if texture_size != (frame_width, frame_height):
                        GL.glTexImage2D(
                            GL.GL_TEXTURE_2D,
                            0,
                            GL.GL_RGB8,
                            frame_width,
                            frame_height,
                            0,
                            GL.GL_RGB,
                            GL.GL_UNSIGNED_BYTE,
                            frame,
                        )
                        texture_size = (frame_width, frame_height)
                    else:
                        GL.glTexSubImage2D(
                            GL.GL_TEXTURE_2D,
                            0,
                            0,
                            0,
                            frame_width,
                            frame_height,
                            GL.GL_RGB,
                            GL.GL_UNSIGNED_BYTE,
                            frame,
                        )
                    self.frame_times.append(time.perf_counter())

                framebuffer_width, framebuffer_height = glfw.get_framebuffer_size(
                    window
                )
                GL.glViewport(0, 0, framebuffer_width, framebuffer_height)
                GL.glClearColor(0.0, 0.0, 0.0, 1.0)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                if texture_size is not None:
                    GL.glUseProgram(program)
                    GL.glBindVertexArray(vao)
                    GL.glActiveTexture(GL.GL_TEXTURE0)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
                    GL.glUniform1i(
                        GL.glGetUniformLocation(program, "source_texture"), 0
                    )
                    GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
                glfw.swap_buffers(window)
        except Exception as error:
            self.error = error
            self.ready_event.set()
        finally:
            if glfw is not None and window is not None:
                try:
                    from OpenGL import GL

                    if texture:
                        GL.glDeleteTextures([texture])
                    if vbo:
                        GL.glDeleteBuffers(1, [vbo])
                    if vao:
                        GL.glDeleteVertexArrays(1, [vao])
                    if program:
                        GL.glDeleteProgram(program)
                except Exception:
                    pass
                glfw.destroy_window(window)
            if glfw is not None:
                glfw.terminate()
            self.closed_event.set()
            self.ready_event.set()

    @staticmethod
    def _compile_shader(GL, shader_type, source):
        shader = GL.glCreateShader(shader_type)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)
        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            raise RuntimeError(
                GL.glGetShaderInfoLog(shader).decode("utf-8", errors="replace")
            )
        return shader

    @classmethod
    def _create_program(cls, GL):
        vertex = cls._compile_shader(
            GL,
            GL.GL_VERTEX_SHADER,
            """
            #version 330 core
            layout(location = 0) in vec2 position;
            layout(location = 1) in vec2 in_uv;
            out vec2 uv;
            void main() {
                uv = in_uv;
                gl_Position = vec4(position, 0.0, 1.0);
            }
            """,
        )
        fragment = cls._compile_shader(
            GL,
            GL.GL_FRAGMENT_SHADER,
            """
            #version 330 core
            in vec2 uv;
            uniform sampler2D source_texture;
            out vec4 out_color;
            void main() {
                out_color = vec4(
                    texture(source_texture, vec2(uv.x, 1.0 - uv.y)).rgb,
                    1.0
                );
            }
            """,
        )
        program = GL.glCreateProgram()
        GL.glAttachShader(program, vertex)
        GL.glAttachShader(program, fragment)
        GL.glLinkProgram(program)
        GL.glDeleteShader(vertex)
        GL.glDeleteShader(fragment)
        if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
            raise RuntimeError(
                GL.glGetProgramInfoLog(program).decode("utf-8", errors="replace")
            )
        return program

    @staticmethod
    def _create_quad(GL):
        vertices = np.asarray(
            [
                -1.0,
                -1.0,
                0.0,
                0.0,
                1.0,
                -1.0,
                1.0,
                0.0,
                -1.0,
                1.0,
                0.0,
                1.0,
                1.0,
                1.0,
                1.0,
                1.0,
            ],
            dtype=np.float32,
        )
        vao = GL.glGenVertexArrays(1)
        vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(
            GL.GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL.GL_STATIC_DRAW
        )
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(
            0, 2, GL.GL_FLOAT, False, 4 * vertices.itemsize, None
        )
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(
            1,
            2,
            GL.GL_FLOAT,
            False,
            4 * vertices.itemsize,
            ctypes.c_void_p(2 * vertices.itemsize),
        )
        return vao, vbo
