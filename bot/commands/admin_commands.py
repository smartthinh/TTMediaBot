from __future__ import annotations
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional, TYPE_CHECKING
from queue import Empty

from bot.commands.command import Command
from bot.player.enums import State, TrackType
from bot.player.track import Track
from bot.TeamTalk.structs import UserType
from bot import app_vars, errors, utils

if TYPE_CHECKING:
    from bot.TeamTalk.structs import User


class AdminHelpCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Shows administrator commands")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not (user.is_admin or user.type == UserType.Admin):
            raise errors.AccessDeniedError(
                self.translator.translate("Only administrators can use this command")
            )
        return self.command_processor.admin_help(arg, user)


class BlockCommandCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "+/-COMMAND Blocks or unblocks commands. +COMMAND adds command to the blocklist. -COMMAND removes from it. Without a command shows the blocklist"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        arg = arg.lower()
        if len(arg) >= 1 and arg[1:] not in self.command_processor.commands_dict:
            raise errors.InvalidArgumentError()
        if not arg:
            return (
                ", ".join(self.config.general.blocked_commands)
                if self.config.general.blocked_commands
                else self.translator.translate("The list is empty")
            )
        if arg[0] == "+":
            if arg[1::] not in self.config.general.blocked_commands:
                self.config.general.blocked_commands.append(arg[1::])
                return self.translator.translate("Added")
            else:
                return self.translator.translate("This command is already added")
        elif arg[0] == "-":
            if arg[1::] in self.config.general.blocked_commands:
                del self.config.general.blocked_commands[
                    self.config.general.blocked_commands.index(arg[1::])
                ]
                return self.translator.translate("Deleted")
            else:
                return self.translator.translate("This command is not blocked")
        else:
            raise errors.InvalidArgumentError()


class ChangeGenderCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "GENDER Changes bot's gender. n neutral, m male, f female"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        try:
            self.ttclient.change_gender(arg)
            self.config.teamtalk.gender = arg
        except KeyError:
            raise errors.InvalidArgumentError()


class ChangeLanguageCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("LANGUAGE Changes bot's language")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if arg:
            try:
                self.translator.set_locale(arg)
                self.config.general.language = arg
                self.ttclient.change_status_text("")
                return self.translator.translate("The language has been changed")
            except errors.LocaleNotFoundError:
                return self.translator.translate("Incorrect language")
        else:
            return self.translator.translate(
                "Current language: {current_language}. Available languages: {available_languages}"
            ).format(
                current_language=self.translator.get_locale(),
                available_languages=", ".join(self.translator.get_locales()),
            )


class ChangeNicknameCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("NICKNAME Changes bot's nickname")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self.ttclient.change_nickname(arg)
        self.config.teamtalk.nickname = arg


class ClearCacheCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "r/f Clears bot's cache. r clears recents, f clears favorites, without an option clears the entire cache"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not arg:
            self.cache.recents.clear()
            self.cache.favorites.clear()
            self.cache_manager.save()
            return self.translator.translate("Cache cleared")
        elif arg == "r":
            self.cache.recents.clear()
            self.cache_manager.save()
            return self.translator.translate("Recents cleared")
        elif arg == "f":
            self.cache.favorites.clear()
            self.cache_manager.save()
            return self.translator.translate("Favorites cleared")


class JoinChannelCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            'Join channel. first argument is channel name or id, second argument is password, split argument " | ", if password is undefined, don\'t type second argument'
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        args = self.command_processor.split_arg(arg)
        if not arg:
            channel = self.config.teamtalk.channel
            password = self.config.teamtalk.channel_password
        elif len(args) == 2:
            channel = args[0]
            password = args[1]
        else:
            channel = arg
            password = ""
        if isinstance(channel, str) and channel.isdigit():
            channel = int(channel)
        try:
            cmd = self.ttclient.join_channel(channel, password)
        except ValueError:
            return self.translator.translate("This channel does not exist")
        while True:
            try:
                event = self.ttclient.event_success_queue.get_nowait()
                if event.source == cmd:
                    break
                else:
                    self.ttclient.event_success_queue.put(event)
            except Empty:
                pass
            try:
                error = self.ttclient.errors_queue.get_nowait()
                if error.command_id == cmd:
                    return self.translator.translate(
                        "Error joining channel: {error}".format(error=error.message)
                    )
                else:
                    self.ttclient.errors_queue.put(error)
            except Empty:
                pass
            time.sleep(app_vars.loop_timeout)


""" class TaskSchedulerCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Task scheduler")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if arg[0] == "+":
            self._add(arg[1::])

    def _add(self, arg: str) -> None:
        args = arg.split("|")
        timestamp = self._get_timestamp(args[0])
        task = []
        for arg in args[1::]:
            try:
                command, arg = self.parse_command(message.text)
                if self.check_access(message.user, command):
                    command = self.get_command(command, message.user)
                    task.append((command, arg))
            except errors.AccessDeniedError as e:
                return e
            except (errors.ParseCommandError, errors.UnknownCommandError):
                return self.translator.translate("Unknown command. Send \"h\" for help.")
            except errors.InvalidArgumentError:
                return self.help(command, message.user)
        if timestamp in self.module_manager.task_scheduler.tasks:
            self.module_manager.task_scheduler[timestamp].append(task)
        else:
            self.module_manager.task_scheduler.tasks[timestamp] = [task,]


    def _get_timestamp(self, t):
        return int(datetime.combine(datetime.today(), datetime.strptime(t, self.config["general"]["time_format"]).time()).timestamp())
 """


class VoiceTransmissionCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Enables or disables voice transmission")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not self.ttclient.is_voice_transmission_enabled:
            self.ttclient.enable_voice_transmission()
            if self.player.state == State.Stopped:
                self.ttclient.change_status_text(
                    self.translator.translate("Voice transmission enabled")
                )
            return self.translator.translate("Voice transmission enabled")
        else:
            self.ttclient.disable_voice_transmission()
            if self.player.state == State.Stopped:
                self.ttclient.change_status_text("")
            return self.translator.translate("Voice transmission disabled")


class LockCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Locks or unlocks the bot")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self.command_processor.locked = not self.command_processor.locked
        return (
            self.translator.translate("Locked")
            if self.command_processor.locked
            else self.translator.translate("Unlocked")
        )


class ChangeStatusCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("STATUS Changes bot's status")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self.ttclient.change_status_text(arg)
        self.config.teamtalk.status = arg


class EventHandlingCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Enables or disables event handling")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self.config.teamtalk.event_handling.load_event_handlers = (
            not self.config.teamtalk.event_handling.load_event_handlers
        )
        return (
            self.translator.translate("Event handling is enabled")
            if self.config.teamtalk.event_handling.load_event_handlers
            else self.translator.translate("Event handling is disabled")
        )


class ChannelMessagesCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "Toggles channel messages. When disabled, notifications are sent privately instead of public channel"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self.config.general.send_channel_messages = (
            not self.config.general.send_channel_messages
        )
        self.config_manager.save()
        if self.config.general.send_channel_messages:
            return self.translator.translate(
                "Public channel messages enabled (bot will announce in channel)."
            )
        else:
            return self.translator.translate(
                "Private messages mode enabled (bot will send notifications privately instead of public channel)."
            )


class SaveConfigCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Saves bot's configuration")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self.config_manager.save()
        return self.translator.translate("Configuration saved")


class AdminUsersCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "+/-USERNAME Manages a list of administrators. +USERNAME adds a user. -USERNAME removes it. Without an option shows the list"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if arg:
            if arg[0] == "+":
                self.config.teamtalk.users.admins.append(arg[1::])
                return self.translator.translate("Added")
            elif arg[0] == "-":
                try:
                    del self.config.teamtalk.users.admins[
                        self.config.teamtalk.users.admins.index(arg[1::])
                    ]
                    return self.translator.translate("Deleted")
                except ValueError:
                    return self.translator.translate(
                        "This user is not in the admin list"
                    )
        else:
            admins = self.config.teamtalk.users.admins.copy()
            if len(admins) > 0:
                if "" in admins:
                    admins[admins.index("")] = self.translator.translate("<Anonymous>")
                return ", ".join(admins)
            else:
                return self.translator.translate("The list is empty")


class BannedUsersCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "+/-USERNAME Manages a list of banned users. +USERNAME adds a user. -USERNAME removes it. Without an option shows the list"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if arg:
            if arg[0] == "+":
                self.config.teamtalk.users.banned_users.append(arg[1::])
                return self.translator.translate("Added")
            elif arg[0] == "-":
                try:
                    del self.config.teamtalk.users.banned_users[
                        self.config.teamtalk.users.banned_users.index(arg[1::])
                    ]
                    return self.translator.translate("Deleted")
                except ValueError:
                    return self.translator.translate("This user is not banned")
        else:
            banned_users = self.config.teamtalk.users.banned_users.copy()
            if len(banned_users) > 0:
                if "" in banned_users:
                    banned_users[banned_users.index("")] = self.translator.translate("<Anonymous>")
                return ", ".join(banned_users)
            else:
                return self.translator.translate("The list is empty")


class QuitCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Quits the bot")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self._bot.close()


class RestartCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Restarts the bot")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        self._bot.close()
        args = sys.argv
        if sys.platform == "win32":
            subprocess.run([sys.executable] + args)
        else:
            args.insert(0, sys.executable)
            os.execv(sys.executable, args)


class GetChannelIDCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate("Returns current channel's ID")

    def __call__(self, arg: str, user: User) -> Optional[str]:
        return str(self.ttclient.channel.id)


class DownloadLocalAudioCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "[QUERY|LINK] Downloads the current track or specified link/query as audio and saves it to the downloads folder."
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not (user.is_admin or user.type == UserType.Admin):
            raise errors.AccessDeniedError(
                self.translator.translate("Only administrators can use this command")
            )
        if arg:
            threading.Thread(
                target=self._process_query,
                args=(arg, user),
                daemon=True,
            ).start()
            return self.translator.translate("Searching and downloading audio...")
        else:
            if self.player.state != State.Stopped:
                track = self.player.track
                if track.url and track.type != TrackType.Live:
                    threading.Thread(
                        target=self._process_track,
                        args=(track, user),
                        daemon=True,
                    ).start()
                    return self.translator.translate("Downloading audio...")
                else:
                    return self.translator.translate("Live streams cannot be downloaded")
            else:
                return self.translator.translate("Nothing is playing")

    def _process_query(self, query: str, user: User) -> None:
        try:
            tracks = self.module_manager.streamer.get(query, user.is_admin)
            if not tracks:
                self.ttclient.send_message(
                    self.translator.translate("Nothing is found for your query"),
                    user,
                )
                return
            self._process_track(tracks[0], user)
        except Exception as e:
            logging.error(f"DownloadLocalAudio error for query '{query}': {e}", exc_info=True)
            self.ttclient.send_message(
                self.translator.translate("Error: {}").format(str(e)),
                user,
            )

    def _process_track(self, track: Track, user: User) -> None:
        try:
            dest_dir = app_vars.music_downloads_dir
            os.makedirs(dest_dir, exist_ok=True)
            if track.type == TrackType.Live:
                self.ttclient.send_message(
                    self.translator.translate("Live streams cannot be downloaded"),
                    user,
                )
                return

            if track.type == TrackType.Local:
                source_file = track.url
                if not os.path.exists(source_file):
                    self.ttclient.send_message(
                        self.translator.translate("Error: File not found."),
                        user,
                    )
                    return
                dest_file = os.path.join(dest_dir, os.path.basename(source_file))
                shutil.copy2(source_file, dest_file)
                file_path = dest_file
            else:
                if track.type == TrackType.Dynamic:
                    _ = track.url
                file_path = track.download(dest_dir, video=False)
                if not os.path.exists(file_path):
                    base_path = file_path.rsplit(".", 1)[0]
                    for ext in ["mp3", "m4a", "opus", "webm", "wav", "flac"]:
                        if os.path.exists(f"{base_path}.{ext}"):
                            file_path = f"{base_path}.{ext}"
                            break
                if not os.path.exists(file_path):
                    self.ttclient.send_message(
                        self.translator.translate("Error: Downloaded file not found."),
                        user,
                    )
                    return

            target_format = getattr(self.config.general, "download_audio_format", "default")
            target_rate = getattr(self.config.general, "sampling_rate", "default")
            target_bitrate = getattr(self.config.general, "mp3_bitrate", "320k")
            file_path = utils.convert_audio(file_path, target_format, target_rate, target_bitrate)

            file_name = os.path.basename(file_path)
            self.ttclient.send_message(
                self.translator.translate(
                    "Audio downloaded successfully: {file_name}\nSaved to: {path}"
                ).format(file_name=file_name, path=file_path),
                user,
            )
        except Exception as e:
            logging.error(f"DownloadLocalAudio error for track '{track.name}': {e}", exc_info=True)
            self.ttclient.send_message(
                self.translator.translate("Error downloading {name}: {error}").format(
                    name=track.name, error=str(e)
                ),
                user,
            )


class DownloadLocalVideoCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "[QUERY|LINK] Downloads the current track or specified link/query as video and saves it to the downloads folder."
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not (user.is_admin or user.type == UserType.Admin):
            raise errors.AccessDeniedError(
                self.translator.translate("Only administrators can use this command")
            )
        if arg:
            threading.Thread(
                target=self._process_query,
                args=(arg, user),
                daemon=True,
            ).start()
            return self.translator.translate("Searching and downloading video...")
        else:
            if self.player.state != State.Stopped:
                track = self.player.track
                if track.url and track.type != TrackType.Live:
                    threading.Thread(
                        target=self._process_track,
                        args=(track, user),
                        daemon=True,
                    ).start()
                    return self.translator.translate("Downloading video...")
                else:
                    return self.translator.translate("Live streams cannot be downloaded")
            else:
                return self.translator.translate("Nothing is playing")

    def _process_query(self, query: str, user: User) -> None:
        try:
            tracks = self.module_manager.streamer.get(query, user.is_admin)
            if not tracks:
                self.ttclient.send_message(
                    self.translator.translate("Nothing is found for your query"),
                    user,
                )
                return
            self._process_track(tracks[0], user)
        except Exception as e:
            logging.error(f"DownloadLocalVideo error for query '{query}': {e}", exc_info=True)
            self.ttclient.send_message(
                self.translator.translate("Error: {}").format(str(e)),
                user,
            )

    def _process_track(self, track: Track, user: User) -> None:
        try:
            dest_dir = app_vars.video_downloads_dir
            os.makedirs(dest_dir, exist_ok=True)
            if track.type == TrackType.Live:
                self.ttclient.send_message(
                    self.translator.translate("Live streams cannot be downloaded"),
                    user,
                )
                return

            if track.type == TrackType.Local:
                source_file = track.url
                if not os.path.exists(source_file):
                    self.ttclient.send_message(
                        self.translator.translate("Error: File not found."),
                        user,
                    )
                    return
                dest_file = os.path.join(dest_dir, os.path.basename(source_file))
                shutil.copy2(source_file, dest_file)
                file_path = dest_file
            else:
                if track.type == TrackType.Dynamic:
                    _ = track.url
                file_path = track.download(dest_dir, video=True)
                if not os.path.exists(file_path):
                    base_path = file_path.rsplit(".", 1)[0]
                    for ext in ["mp4", "mkv", "webm", "avi"]:
                        if os.path.exists(f"{base_path}.{ext}"):
                            file_path = f"{base_path}.{ext}"
                            break
                if not os.path.exists(file_path):
                    self.ttclient.send_message(
                        self.translator.translate("Error: Downloaded file not found."),
                        user,
                    )
                    return

            file_name = os.path.basename(file_path)
            self.ttclient.send_message(
                self.translator.translate(
                    "Video downloaded successfully: {file_name}\nSaved to: {path}"
                ).format(file_name=file_name, path=file_path),
                user,
            )
        except Exception as e:
            logging.error(f"DownloadLocalVideo error for track '{track.name}': {e}", exc_info=True)
            self.ttclient.send_message(
                self.translator.translate("Error downloading {name}: {error}").format(
                    name=track.name, error=str(e)
                ),
                user,
            )


class UploadFileCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "FILE_PATH Uploads a local file to the current TeamTalk channel"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not (user.is_admin or user.type == UserType.Admin):
            raise errors.AccessDeniedError(
                self.translator.translate("Only administrators can use this command")
            )
        if not arg or not arg.strip():
            return self.translator.translate("Error: Please specify the path of the file to upload.")

        file_path = arg.strip().strip('"\'')
        if not os.path.exists(file_path):
            return self.translator.translate("Error: File not found at '{path}'.").format(
                path=file_path
            )
        if os.path.isdir(file_path):
            return self.translator.translate(
                "Error: The specified path is a folder, not a file. Please provide a direct file path."
            )
        if not os.path.isfile(file_path):
            return self.translator.translate("Error: The specified path is not a valid file.")
        if not os.access(file_path, os.R_OK):
            return self.translator.translate("Error: Cannot read file '{path}'. Permission denied.").format(
                path=file_path
            )
        if not self.ttclient.channel or self.ttclient.channel.id == 0:
            return self.translator.translate("Error: Bot is not currently in any channel.")

        channel_id = self.ttclient.channel.id
        file_name = os.path.basename(file_path)

        threading.Thread(
            target=self._upload_worker,
            args=(file_path, user, channel_id),
            daemon=True,
        ).start()

        return self.translator.translate("Uploading file: {file_name}...").format(
            file_name=file_name
        )

    def _upload_worker(self, file_path: str, user: User, channel_id: int) -> None:
        file_name = os.path.basename(file_path)
        try:
            file_size = os.path.getsize(file_path)
            logging.info(
                f"UploadFileCommand: Starting upload of '{file_path}' ({file_size} bytes) to channel {channel_id} requested by {user.username}"
            )
            command_id = self.ttclient.send_file(channel_id, file_path)
            error_exit = False

            while True:
                try:
                    file = self.ttclient.uploaded_files_queue.get_nowait()
                    if file.name == file_name:
                        logging.info(f"UploadFileCommand: File '{file_name}' successfully uploaded")
                        break
                    else:
                        self.ttclient.uploaded_files_queue.put(file)
                except Empty:
                    pass
                try:
                    error = self.ttclient.errors_queue.get_nowait()
                    if error.command_id == command_id:
                        logging.error(
                            f"UploadFileCommand: Error uploading file: {error.message} (Type: {error.type})"
                        )
                        self.ttclient.send_message(
                            self.translator.translate("Error uploading file: {error}").format(
                                error=error.message
                            ),
                            user,
                        )
                        error_exit = True
                        break
                    else:
                        self.ttclient.errors_queue.put(error)
                except Empty:
                    pass
                time.sleep(app_vars.loop_timeout)

            if error_exit:
                return

            size_str = utils.format_file_size(file_size)
            self.ttclient.send_message(
                self.translator.translate("File uploaded successfully: {file_name} ({size})").format(
                    file_name=file_name, size=size_str
                ),
                user,
            )
        except Exception as e:
            logging.error(f"UploadFileCommand error for file '{file_path}': {e}", exc_info=True)
            self.ttclient.send_message(
                self.translator.translate("Error uploading file: {error}").format(error=str(e)),
                user,
            )


class DeleteFilesCommand(Command):
    @property
    def help(self) -> str:
        return self.translator.translate(
            "[FILE_NAME] Deletes a specific file or all files in the current channel"
        )

    def __call__(self, arg: str, user: User) -> Optional[str]:
        if not (user.is_admin or user.type == UserType.Admin):
            raise errors.AccessDeniedError(
                self.translator.translate("Only administrators can use this command")
            )
        if not self.ttclient.channel or self.ttclient.channel.id == 0:
            return self.translator.translate("Error: Bot is not currently in any channel.")

        channel_id = self.ttclient.channel.id
        files = self.ttclient.get_channel_files(channel_id)
        if not files:
            return self.translator.translate("No files found in the current channel.")

        target = arg.strip().strip('"\'') if arg else ""

        if target:
            # Delete specific file(s)
            matching_files = [f for f in files if f.name.lower() == target.lower()]
            if not matching_files and target.isdigit():
                matching_files = [f for f in files if str(f.id) == target]
            if not matching_files:
                partial_matches = [f for f in files if target.lower() in f.name.lower()]
                if len(partial_matches) == 1:
                    matching_files = partial_matches

            if not matching_files:
                return self.translator.translate(
                    "Error: File '{name}' not found in the current channel."
                ).format(name=target)

            deleted_files = []
            failed_files = []
            for file in matching_files:
                try:
                    self.ttclient.delete_file(channel_id, file.id)
                    deleted_files.append(file.name)
                    time.sleep(0.02)
                except Exception as e:
                    logging.error(
                        f"DeleteFilesCommand: Failed to delete file '{file.name}' (id: {file.id}): {e}"
                    )
                    failed_files.append(file.name)

            if failed_files and not deleted_files:
                return self.translator.translate("Error deleting file '{name}'.").format(
                    name=target
                )
            elif len(deleted_files) == 1:
                return self.translator.translate("Successfully deleted file: {name}").format(
                    name=deleted_files[0]
                )
            else:
                return self.translator.translate(
                    "Successfully deleted {count} file(s) matching '{name}'."
                ).format(count=len(deleted_files), name=target)

        # No argument: delete all files
        deleted_count = 0
        failed_count = 0
        for file in files:
            try:
                self.ttclient.delete_file(channel_id, file.id)
                deleted_count += 1
                time.sleep(0.02)
            except Exception as e:
                logging.error(
                    f"DeleteFilesCommand: Failed to delete file '{file.name}' (id: {file.id}): {e}"
                )
                failed_count += 1

        if failed_count == 0:
            return self.translator.translate(
                "Successfully deleted all {count} file(s) from the channel."
            ).format(count=deleted_count)
        else:
            return self.translator.translate(
                "Deleted {deleted} file(s), failed to delete {failed} file(s)."
            ).format(deleted=deleted_count, failed=failed_count)


DeleteAllFilesCommand = DeleteFilesCommand
