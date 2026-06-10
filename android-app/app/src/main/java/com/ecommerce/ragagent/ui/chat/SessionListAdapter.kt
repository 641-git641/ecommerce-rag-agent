package com.ecommerce.ragagent.ui.chat

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ecommerce.ragagent.R
import com.ecommerce.ragagent.data.model.SessionSummary

class SessionListAdapter(
    private val sessions: MutableList<SessionSummary> = mutableListOf()
) : RecyclerView.Adapter<SessionListAdapter.ViewHolder>() {

    var onSessionClick: ((SessionSummary) -> Unit)? = null
    var onSessionDelete: ((SessionSummary) -> Unit)? = null

    fun updateSessions(newList: List<SessionSummary>) {
        sessions.clear()
        sessions.addAll(newList)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_session, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        holder.bind(sessions[position])
    }

    override fun getItemCount(): Int = sessions.size

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val tvTitle: TextView = itemView.findViewById(R.id.tv_session_title)
        private val tvDate: TextView = itemView.findViewById(R.id.tv_session_date)
        private val btnDelete: View = itemView.findViewById(R.id.btn_session_delete)

        fun bind(session: SessionSummary) {
            tvTitle.text = session.displayTitle
            tvDate.text = "${session.displayDate} · ${session.msg_count}条"
            itemView.setOnClickListener {
                onSessionClick?.invoke(session)
            }
            btnDelete.setOnClickListener {
                onSessionDelete?.invoke(session)
            }
        }
    }
}
